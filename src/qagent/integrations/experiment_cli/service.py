"""Submit experiments through CLI v1 and reconcile durable execution records."""

import hashlib
import json
import threading
import time
from uuid import uuid4

from qagent.core.experiment_journal import ACTIVE_STATES, ExperimentJournal
from qagent.experiments.registry import EXPERIMENTS, get_definition
from qagent.integrations.experiment_cli.client import ExperimentCLI
from qagent.integrations.experiment_cli.protocol import (
    TERMINAL_STATES, Event, Progress, check_identity, checked_path, decode_object,
)


class CLIExperimentService(ExperimentJournal):
    def __init__(self, store, settings, *, on_event=None):
        self.store, self.settings = store, settings
        self.client = ExperimentCLI(settings)
        self.on_event = on_event or (lambda event: None)
        self.identity = hashlib.sha256(("experiment_cli:" + settings.model_dump_json()).encode()).hexdigest()
        self._workers = {}
        self._pause = threading.Event()
        self._lifecycle = threading.Lock()
        self._capabilities = None
        self._data_origin = "unreported"
        self._init_journal()

    def describe(self):
        if self._capabilities is None:
            response = self.client.query("describe")
            items = response.get("experiments")
            if response.get("protocol_version") != "1" or not isinstance(items, list):
                raise ValueError("实验 CLI describe 响应不符合协议 v1")
            origin = response.get("data_origin")
            if origin in ("hardware", "test"):
                self._data_origin = origin
            supported = {}
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise ValueError("实验 CLI 返回了无效的实验定义")
                name = item["name"]
                if name in EXPERIMENTS:
                    definition = get_definition(name)
                    defaults = definition.parameters.model_validate(item.get("defaults", {})).model_dump()
                    supported[name] = {
                        "name": name, "description": definition.description, "defaults": defaults,
                        "parameters": definition.parameters.model_json_schema(),
                    }
            self._capabilities = supported
        return {
            "backend": "experiment_cli", "protocol_version": "1",
            "data_origin": self._data_origin, "data_type": "iq", "iq_mode": "single_shot",
            "allowed_qubits": self.settings.allowed_qubits,
            "max_experiments_per_session": self.settings.max_experiments,
            "max_measurements_per_session": self.settings.max_measurements,
            "result_directory": str(self.settings.output_root),
            "experiments": list(self._capabilities.values()),
            "note": "通过独立实验 CLI 执行。每条指令最多启动一个新实验。",
        }

    def _directory(self, experiment_id):
        root = self.settings.output_root.resolve()
        directory = root / experiment_id
        if directory.resolve() != directory:
            raise ValueError("实验目录不能是重定向路径")
        return directory

    def _emit(self, event):
        try:
            self.on_event(event)
        except Exception:
            pass  # Presentation must not affect hardware execution.

    def _update(self, experiment_id, fields):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT report FROM quantumx_hardware_experiments WHERE id=?",
                             (experiment_id,)).fetchone()
            report = json.loads(row[0])
            if report["status"] in TERMINAL_STATES:
                return report
            if "seq" in fields and fields["seq"] < report["seq"]:
                return report
            self._checked_progress(report, fields)
            report.update(fields)
            db.execute("UPDATE quantumx_hardware_experiments SET report=? WHERE id=?",
                       (json.dumps(report, ensure_ascii=False, allow_nan=False), experiment_id))
        return report

    def _checked_progress(self, report, values):
        progress = Progress.model_validate(values).model_dump(exclude_unset=True)
        for count, total in (("scan_points_done", "scan_points_total"), ("iq_groups_done", "iq_groups_total")):
            if total in progress and progress[total] != report[total]:
                raise ValueError("实验进度总数与校验计划不一致")
            if count in progress:
                done, limit = progress[count], report[total]
                if done is None or done < report[count] or (limit is not None and done > limit):
                    raise ValueError("实验进度计数超出计划或发生回退")
        shots = progress.get("shots_done", report["shots_done"])
        groups = progress.get("iq_groups_done", report["iq_groups_done"])
        if shots != groups * report["parameters"]["shots"] or shots > report["reserved_measurements"]:
            raise ValueError("累计 shots 与采集组数或预算不一致")
        return progress

    def _result(self, report, directory, status, progress):
        result_path = checked_path(directory, str(directory / "result.json"))
        result = decode_object(result_path.read_text(encoding="utf-8"))
        check_identity(result, report["experiment_id"])
        if (result.get("operation_key"), result.get("experiment"), result.get("status")) != (
            report["operation_key"], report["experiment_type"], status
        ):
            raise ValueError("实验结果清单与本次请求不匹配")
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("实验结果缺少产物清单")
        raw, plots = [], []
        for item in artifacts:
            if not isinstance(item, dict):
                raise ValueError("实验产物条目格式无效")
            path = checked_path(directory, item.get("path"))
            if not path.is_file():
                raise ValueError("实验产物清单引用了缺失文件")
            if item.get("role") == "raw_iq":
                raw.append(path)
            elif item.get("role") == "plot":
                plots.append(path)
        if status == "completed" and not raw:
            raise ValueError("已完成实验缺少原始 IQ 文件")
        if status == "completed" and any(
            type(result.get(key)) is not int or result[key] != progress[key]
            for key in ("scan_points_done", "iq_groups_done", "shots_done")
        ):
            raise ValueError("结果清单的最终采集计数与状态不一致")
        metrics, quality = result.get("metrics", {}), result.get("quality", {})
        if not isinstance(metrics, dict) or not isinstance(quality, dict):
            raise ValueError("实验结果的指标或质量字段格式无效")
        return {
            "result_ref": str(result_path), "metrics": metrics, "quality": quality,
            "raw_data_dir": str(raw[0].parent) if raw else None,
            "raw_files": [str(path) for path in raw],
            "plot_dir": str(plots[0].parent) if plots else None,
            "plot_files": [str(path) for path in plots],
            "warnings": ["实验结果未提供图像"] if status == "completed" and not plots else [],
        }

    def _refresh(self, report):
        if report["status"] in TERMINAL_STATES:
            return report
        experiment_id = report["experiment_id"]
        try:
            directory = self._directory(experiment_id)
            path = checked_path(directory, str(directory / "status.json"))
            if not path.is_file():
                return report
            state = decode_object(path.read_text(encoding="utf-8"))
            check_identity(state, experiment_id)
            status, seq = state.get("status"), state.get("seq")
            if (not isinstance(status, str) or status not in {"running", "cancelling"} | TERMINAL_STATES
                    or type(seq) is not int or seq < 1):
                raise ValueError("实验状态或序号无效")
            if seq < report["seq"]:
                return report
            progress = self._checked_progress(report, state)
            fields = {"status": status, "seq": seq, **progress}
            if seq > report["seq"]:
                fields["last_event_at"] = min(time.time(), path.stat().st_mtime)
            if status in TERMINAL_STATES:
                if status == "completed":
                    if any(field not in progress for field in ("scan_points_done", "iq_groups_done", "shots_done")):
                        raise ValueError("已完成实验缺少最终采集计数")
                    for done, total in (("scan_points_done", "scan_points_total"), ("iq_groups_done", "iq_groups_total")):
                        if report[total] is not None and progress[done] != report[total]:
                            raise ValueError("已完成实验的计数与计划不一致")
                if status == "completed" or (directory / "result.json").exists():
                    fields.update(self._result(report, directory, status, progress))
                fields.update(finished_at=time.time(), error_type=None if status != "failed" else "ExperimentFailed")
            return self._update(experiment_id, fields)
        except (ValueError, OSError):
            return {**report, "status": "unknown", "execution_note": "执行状态或产物尚未通过核对，请检查实验目录。"}

    def public_result(self, experiment_id):
        report = self.get(experiment_id)
        active = self.is_active(experiment_id)
        recent = time.time() - report["last_event_at"] <= 3 * self.settings.heartbeat_interval_seconds
        if not (active and recent and report["status"] in {"running", "cancelling"}):
            report = self._refresh(report)
        with self.store.connect() as db:
            stopped = db.execute("SELECT stopped FROM quantumx_hardware_experiments WHERE id=?",
                                 (experiment_id,)).fetchone()[0]
        if report["status"] in ACTIVE_STATES and time.time() - report["last_event_at"] > 3 * self.settings.heartbeat_interval_seconds:
            report.update(status="unknown", execution_note="未收到近期心跳或进度，请核对同一任务。")
        result = {key: value for key, value in report.items() if key != "operation_key"}
        return {**result, "executor_active_here": active, "stop_requested": bool(stopped),
                "iq_acquisitions": report["iq_groups_done"], "actual_measurements": report["shots_done"]}

    def start(self, name, parameters, session_id, call_id):
        self.describe()
        if name not in self._capabilities:
            raise ValueError("实验 CLI 未声明支持该实验")
        definition = get_definition(name)
        params = definition.parameters.model_validate(self._capabilities[name]["defaults"] | parameters)
        if params.n not in self.settings.allowed_qubits:
            raise ValueError("目标比特超出 allowed_qubits 约定")
        self.bind_session(session_id)
        signature = json.dumps({"name": name, "parameters": params.model_dump()}, sort_keys=True, allow_nan=False)
        key = self._key(session_id, call_id)
        with self.store.run_lock("quantumx-dispatch"):
            if self._pause.is_set():
                raise ValueError("会话正在退出，不能启动新实验")
            with self.store.connect() as db:
                prior = db.execute("SELECT id,signature,identity FROM quantumx_hardware_experiments WHERE operation_key=?",
                                   (key,)).fetchone()
                rows = db.execute("SELECT id,session_id,cost,report,identity FROM quantumx_hardware_experiments").fetchall()
            if prior:
                if prior[1:] != (signature, self.identity):
                    raise ValueError("同一工具调用编号不能用于不同实验")
                return self.public_result(prior[0])
            for existing_id, _, _, saved, identity in rows:
                report = json.loads(saved)
                if report["status"] in ACTIVE_STATES:
                    if identity != self.identity or self.public_result(existing_id)["status"] in ACTIVE_STATES:
                        raise ValueError("已有未结束或待核对的实验，请先查询或停止")
            own = [row for row in rows if row[1] == session_id and row[4] == self.identity]
            used = sum(row[2] for row in own)
            if len(own) >= self.settings.max_experiments or used + definition.measurement_cost(params) > self.settings.max_measurements:
                raise ValueError("实验将超过本会话预算")
            experiment_id = uuid4().hex
            directory = self._directory(experiment_id)
            reference = params.reference_experiment_id
            input_refs = {}
            iteration = 0
            if reference:
                prior_result = self.public_result(reference)
                if (prior_result["status"] != "completed" or prior_result["n"] != params.n
                        or prior_result.get("quality", {}).get("accepted") is not True):
                    raise ValueError("引用实验必须属于同一目标且已完成质量检查")
                input_refs["candidate_result"] = prior_result["result_ref"]
                iteration = prior_result["iteration_index"] + 1
            request = {
                "protocol_version": "1", "experiment_id": experiment_id, "operation_key": key,
                "experiment": name, "parameters": params.model_dump(exclude={"reference_experiment_id"}),
                "input_refs": input_refs, "output_directory": str(directory),
                "parent_experiment_id": reference,
                "iteration_index": iteration,
            }
            self.store._write(self.settings.output_root, experiment_id, "request.json", request)
            request_file = directory / "request.json"
            plan = self.client.query("validate", "--request", request_file)
            check_identity(plan, experiment_id)
            counts = Progress.model_validate(plan)
            cost = plan.get("planned_measurements")
            if (type(cost) is not int or cost <= 0 or
                    (counts.iq_groups_total is not None and cost != counts.iq_groups_total * params.shots)):
                raise ValueError("实验 CLI 未返回有效的计划采集预算")
            if used + cost > self.settings.max_measurements:
                raise ValueError("实际计划将超过本会话预算")
            report = {
                "experiment_id": experiment_id, "operation_key": key, "experiment_type": name,
                "backend": "experiment_cli", "data_origin": self._data_origin,
                "n": params.n, "parameters": params.model_dump(), "artifact_directory": str(directory),
                "request_path": str(request_file), "status": "starting", "seq": 0,
                "reserved_measurements": cost, "created_at": time.time(), "last_event_at": time.time(),
                "scan_points_total": counts.scan_points_total, "iq_groups_total": counts.iq_groups_total,
                "scan_points_done": 0, "iq_groups_done": 0, "shots_done": 0,
                "iteration_index": request["iteration_index"], "parent_experiment_id": reference,
            }
            accepted = threading.Event()
            worker = threading.Thread(target=self._execute, args=(experiment_id, request_file, accepted), daemon=True)
            with self._lifecycle:
                if self._pause.is_set():
                    raise ValueError("会话正在退出，不能启动新实验")
                with self.store.connect() as db:
                    db.execute("INSERT INTO quantumx_hardware_experiments(id,operation_key,session_id,identity,signature,cost,report)"
                               " VALUES(?,?,?,?,?,?,?)", (experiment_id, key, session_id, self.identity, signature,
                                                          cost, json.dumps(report, allow_nan=False)))
                self._workers[experiment_id] = worker
                worker.start()
        accepted.wait(self.settings.start_timeout_seconds)
        receipt = self.public_result(experiment_id)
        if receipt["status"] == "starting":
            receipt.update(status="unknown", execution_note="尚未确认实验端接受任务，请查询同一任务。")
        return receipt

    def _execute(self, experiment_id, request_file, accepted):
        process = None
        try:
            logs = self.store.root / "experiment-cli"
            logs.mkdir(exist_ok=True)
            with (logs / (experiment_id + ".stderr.log")).open("wb") as stderr:
                process = self.client.run(request_file, stderr)
                with process.stdout:
                    for line in process.stdout:
                        try:
                            data = decode_object(line)
                            seq = data.get("seq")
                            check_identity(data, experiment_id)
                            if type(seq) is int and seq > self.get(experiment_id)["seq"] + 1:
                                self._replay(experiment_id, seq, accepted)
                            self._handle_event(experiment_id, data, accepted)
                        except ValueError:
                            self._update(experiment_id, {"protocol_error": True})
                exit_code = process.wait()
            report = self._refresh(self.get(experiment_id))
            if report["status"] not in TERMINAL_STATES:
                report = self._update(experiment_id, {"status": "unknown", "exit_code": exit_code,
                                                       "error_type": "UnconfirmedTerminalState"})
        except Exception as exc:
            report = self._update(experiment_id, {
                "status": "failed" if process is None else "unknown", "error_type": type(exc).__name__,
            })
        finally:
            accepted.set()
        self._emit({"event": "experiment_finished", **report})

    def _replay(self, experiment_id, before_seq, accepted):
        directory = self._directory(experiment_id)
        try:
            path = checked_path(directory, str(directory / "events.jsonl"))
            with path.open(encoding="utf-8") as events:
                for line in events:
                    data = decode_object(line)
                    seq = data.get("seq")
                    if type(seq) is int and seq >= before_seq:
                        break
                    self._handle_event(experiment_id, data, accepted)
        except (ValueError, OSError):
            self._update(experiment_id, {"protocol_error": True})

    def _handle_event(self, experiment_id, data, accepted):
        check_identity(data, experiment_id)
        event = Event.model_validate(data)
        if not isinstance(data.get("timestamp"), str) or event.timestamp.utcoffset() is None:
            raise ValueError("实验事件时间缺少时区")
        report = self.get(experiment_id)
        if event.seq <= report["seq"] or report["status"] in TERMINAL_STATES:
            return
        if event.event in TERMINAL_STATES:
            # Terminal facts are confirmed from status.json and result.json.
            return
        progress = self._checked_progress(report, data)
        status = "cancelling" if report["status"] == "cancelling" else "running"
        report = self._update(experiment_id, {"seq": event.seq, "status": status,
                                              "last_event_at": time.time(), **progress})
        if event.event in {"started", "progress"}:
            label = "experiment_started" if event.event == "started" else "experiment_progress"
            self._emit({"event": label, **report})
        accepted.set()

    def stop(self, experiment_id):
        result = self.public_result(experiment_id)
        if result["status"] in TERMINAL_STATES or result["stop_requested"]:
            return result
        response = self.client.query("stop", "--experiment-dir", self._directory(experiment_id))
        check_identity(response, experiment_id)
        if response.get("stop_requested") is not True:
            result = self.public_result(experiment_id)
            if result["status"] in TERMINAL_STATES:
                return result
            raise ValueError("实验端尚未确认停止请求")
        with self.store.connect() as db:
            db.execute("UPDATE quantumx_hardware_experiments SET stopped=1 WHERE id=?", (experiment_id,))
        return self.public_result(experiment_id)

    def pause(self):
        with self._lifecycle:
            self._pause.set()
            experiments = list(self._workers)
        for experiment_id in experiments:
            if self.is_active(experiment_id):
                try:
                    self.stop(experiment_id)
                except (ValueError, OSError):
                    self._emit({"event": "experiment_finished", "experiment_id": experiment_id,
                                "backend": "experiment_cli", "status": "unknown"})
