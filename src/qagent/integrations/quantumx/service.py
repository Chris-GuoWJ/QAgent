"""Journaled function experiments, separate from the frequency-only controller."""

import copy
import hashlib
import json
import threading
import time
from uuid import uuid4

import numpy as np

from ...config import read_json_config
from ...storage import RunBusy
from .library import load_library
from .parameters import QuantumXSettings
from .recording_backend import ExperimentInterrupted, RecordingBackend
from .registry import EXPERIMENTS, get_definition
from .results import json_ready, summarize
from .simulation import SimulatedFunctionBackend


class QuantumXService:
    def __init__(self, store, settings: QuantumXSettings, backend_factory=None):
        self.store, self.settings = store, settings
        source = settings.source_dir.resolve()
        if store.root == source or source in store.root.parents:
            raise ValueError("QuantumX 结果目录必须位于 QuantumX2 代码库之外")
        self.library, self.config, self.source_hash = load_library(source)
        self.hardware = read_json_config(settings.hardware_config or source / "configs/hardware_tmpl.json")
        self.qubits = read_json_config(settings.qubits_config or source / "configs/qubits_tmpl.json")
        self.backend_factory = backend_factory or SimulatedFunctionBackend
        self.data_origin = "simulated" if backend_factory is None else "injected_backend"
        identity_data = {"source": str(source), "source_hash": self.source_hash,
                         "settings": settings.model_dump(mode="json"), "hardware": self.hardware,
                         "qubits": self.qubits, "data_origin": self.data_origin}
        self.identity = hashlib.sha256(json.dumps(identity_data, sort_keys=True, allow_nan=False).encode()).hexdigest()
        namespace = "quantumx-inputs/" + self.identity
        hardware_ref = store.write_artifact(namespace, "hardware.json", self.hardware)
        qubits_ref = store.write_artifact(namespace, "qubits.json", self.qubits)
        self.config.HardwareConfig.load(store.root / hardware_ref)
        self.original_qubits = self.config.QubitsConfig.load(store.root / qubits_ref)
        for n in settings.allowed_qubits:
            self.original_qubits.get(n)
        self._workers = {}
        self._pause = threading.Event()
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS quantumx_experiments (
                    id TEXT PRIMARY KEY, operation_key TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL, identity TEXT NOT NULL, signature TEXT NOT NULL,
                    cost INTEGER NOT NULL, stopped INTEGER NOT NULL DEFAULT 0, report TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quantumx_sessions (id TEXT PRIMARY KEY, identity TEXT NOT NULL);
            """)

    def bind_session(self, session_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT identity FROM quantumx_sessions WHERE id=?", (session_id,)).fetchone()
            if row and row[0] != self.identity:
                raise ValueError("QuantumX 配置或源码已变化，请使用原配置或新建会话")
            db.execute("INSERT OR IGNORE INTO quantumx_sessions VALUES(?,?)", (session_id, self.identity))

    def describe(self):
        return {"data_origin": self.data_origin, "data_type": "iq", "iq_mode": "single_shot",
                "allowed_qubits": self.settings.allowed_qubits,
                "target_naming": "QuantumX n=1 means q1; no implicit mapping from legacy Q0",
                "max_experiments_per_session": self.settings.max_experiments,
                "max_measurements_per_session": self.settings.max_measurements,
                "experiments": [{"name": d.name, "function": d.function_name, "description": d.description,
                                 "parameters": d.parameters.model_json_schema()} for d in EXPERIMENTS.values()],
                "note": "真实调用 QuantumX 函数，默认输入模拟 IQ；候选参数仅供后续实验副本使用，不写回原配置。Ramsey 是精调，不是宽范围找共振。"}

    @staticmethod
    def _key(session_id, call_id):
        return hashlib.sha256(f"{session_id}:{call_id}".encode()).hexdigest()

    def _save(self, report):
        self.store.write_artifact("quantumx/" + report["experiment_id"], "report.json", report)
        with self.store.connect() as db:
            db.execute("UPDATE quantumx_experiments SET report=? WHERE id=?",
                       (json.dumps(report, ensure_ascii=False, allow_nan=False), report["experiment_id"]))

    def get(self, experiment_id):
        with self.store.connect() as db:
            row = db.execute("SELECT identity,report FROM quantumx_experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row or row[0] != self.identity:
            raise ValueError("找不到当前 QuantumX 配置的实验编号")
        return json.loads(row[1])

    def public_result(self, experiment_id):
        report = self.get(experiment_id)
        if report["status"] in {"queued", "running"}:
            manifest = self.store.root / report["artifact_directory"] / "acquisitions.json"
            if manifest.exists():
                report["iq_records"] = read_json_config(manifest)["iq_records"]
                report["actual_measurements"] = sum(r["shots"] for r in report["iq_records"])
        result = {k: report.get(k) for k in (
            "experiment_id", "experiment_type", "n", "status", "parameters", "data_origin",
            "metrics", "quality", "reserved_measurements", "actual_measurements", "artifact_directory",
            "report_ref", "result_ref", "error_type", "warnings", "created_at", "finished_at")}
        result["iq_acquisitions"] = len(report.get("iq_records", []))
        result["artifact_directory"] = str(self.store.root / report["artifact_directory"])
        result["executor_active_here"] = self.is_active(experiment_id)
        if report["status"] in {"queued", "running"} and not result["executor_active_here"]:
            result["execution_note"] = "本进程无执行器；可能由其他进程运行或已中断。未自动重放，可查询或停止核对。"
        with self.store.connect() as db:
            result["stop_requested"] = bool(db.execute("SELECT stopped FROM quantumx_experiments WHERE id=?", (experiment_id,)).fetchone()[0])
        return result

    def list_results(self, session_id=None):
        with self.store.connect() as db:
            rows = db.execute("SELECT id FROM quantumx_experiments WHERE identity=? "
                              "AND (? IS NULL OR session_id=?) ORDER BY rowid DESC LIMIT 20",
                              (self.identity, session_id, session_id)).fetchall()
        return {"experiments": [self.public_result(row[0]) for row in rows]}

    def recover_operation(self, session_id, call_id):
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM quantumx_experiments WHERE operation_key=? AND identity=?",
                             (self._key(session_id, call_id), self.identity)).fetchone()
        return ({"recovered_receipt": True, **self.public_result(row[0])} if row else
                {"interrupted": True, "message": "未找到已保存回执，未自动重放实验；请先查询已有记录。"})

    def start(self, name, parameters, session_id, call_id):
        definition = get_definition(name)
        params = definition.parameters.model_validate(parameters)
        if params.n not in self.settings.allowed_qubits:
            raise ValueError("目标比特超出 QuantumX 的 allowed_qubits 约定")
        if self._pause.is_set():
            raise ValueError("会话正在退出，不能启动新实验")
        self.bind_session(session_id)
        signature = json.dumps({"name": name, "parameters": params.model_dump()}, sort_keys=True, allow_nan=False)
        key = self._key(session_id, call_id)
        cost = definition.measurement_cost(params)
        with self.store.run_lock("quantumx-dispatch"):
            with self.store.connect() as db:
                row = db.execute("SELECT id,signature,identity FROM quantumx_experiments WHERE operation_key=?", (key,)).fetchone()
                if row:
                    if row[1:] != (signature, self.identity):
                        raise ValueError("同一工具调用编号不能用于不同实验")
                    return self.public_result(row[0])
                rows = db.execute("SELECT session_id,cost,report FROM quantumx_experiments WHERE identity=?", (self.identity,)).fetchall()
            if any(json.loads(row[2])["status"] in {"queued", "running"} for row in rows):
                raise ValueError("已有未结束或待核对的 QuantumX 实验，请先查询或停止")
            own = [r for r in rows if r[0] == session_id]
            if len(own) >= self.settings.max_experiments or sum(r[1] for r in own) + cost > self.settings.max_measurements:
                raise ValueError("实验将超过本会话 QuantumX 预算")
            experiment_id = uuid4().hex
            namespace = "quantumx/" + experiment_id
            context, kwargs, axes = self._prepare(name, params, namespace)
            report = {"schema_version": 1, "experiment_id": experiment_id, "experiment_type": name,
                      "function": definition.function_name, "n": params.n, "parameters": params.model_dump(),
                      "data_origin": self.data_origin, "source_hash": self.source_hash,
                      "status": "queued", "created_at": time.time(), "axes": axes, "iq_records": [],
                      "reserved_measurements": cost, "actual_measurements": 0,
                      "artifact_directory": namespace, "report_ref": namespace + "/report.json",
                      "config_snapshot": {"hardware": namespace + "/hardware.json", "qubits": namespace + "/qubits.json"}}
            with self.store.connect() as db:
                db.execute("INSERT INTO quantumx_experiments(id,operation_key,session_id,identity,signature,cost,report) VALUES(?,?,?,?,?,?,?)",
                           (experiment_id, key, session_id, self.identity, signature, cost, json.dumps(report, allow_nan=False)))
            self._save(report)
            worker = threading.Thread(target=self._execute, args=(report, context, kwargs), daemon=True)
            self._workers[experiment_id] = worker
            worker.start()
        return {"scheduled": True, **self.public_result(experiment_id)}

    def _prepare(self, name, params, namespace):
        qubits = copy.deepcopy(self.qubits)
        if params.reference_experiment_id:
            reference = self.get(params.reference_experiment_id)
            if reference["status"] != "completed" or reference["n"] != params.n or not reference["quality"]["accepted"]:
                raise ValueError("引用实验必须属于同一目标且已完成质量检查")
            qubits = self.store.read_artifact(reference["config_snapshot"]["qubits"])
            metrics = reference["metrics"]
            target = qubits["qubits"][str(params.n)]
            if reference["experiment_type"] == "readout_centers":
                target["readoutCenters"] = {"0": metrics["center0"], "1": metrics["center1"]}
            elif reference["experiment_type"] == "pi_amplitude":
                target["piAmp"] = metrics["best_piAmp"]
            else:
                target["f10"] = metrics["best_f10"]
        hardware_ref = self.store.write_artifact(namespace, "hardware.json", self.hardware)
        qubits_ref = self.store.write_artifact(namespace, "qubits.json", qubits)
        qubit = self.config.QubitsConfig.load(self.store.root / qubits_ref).get(params.n)
        kwargs = {"n": params.n, "shots": params.shots, "update": False, "show_plot": False,
                  "hardware_config": self.store.root / hardware_ref, "qubits_config": self.store.root / qubits_ref}
        if name == "readout_centers":
            labels = [{"state": 0}, {"state": 1}]
            axes = [{"name": "prepared_state", "unit": "state", "values": [0, 1]}]
            kwargs["validation_fraction"] = params.validation_fraction
        elif name == "pi_amplitude":
            # Same conservative DRAG limit used by QuantumX's default Rabi scan.
            length, alpha = qubit.piLen, qubit.alphaDRAG
            safe_limit = 1.0 / (1.0 + np.pi * abs(alpha) / length)
            stop = params.amp_stop if params.amp_stop is not None else min(2 * qubit.piAmp, safe_limit)
            amps = np.linspace(params.amp_start, stop, params.points)
            labels = [{"amplitude": float(a)} for a in amps]
            axes = [{"name": "amplitude", "unit": "normalized", "values": amps.tolist()}]
            kwargs["amps"] = amps
        else:
            delays = np.linspace(params.delay_start_s, params.delay_stop_s, params.points)
            labels = [{"delay_s": float(t), "series": s} for t in delays for s in ("X", "Y")]
            axes = [{"name": "delay", "unit": "s", "values": delays.tolist()},
                    {"name": "series", "unit": "label", "values": ["X", "Y"]}]
            kwargs.update(delays=delays, fringe_freq=params.fringe_freq_hz, nfftpoints=params.nfftpoints)
        context = {"experiment_type": name, "parameters": params.model_dump(), "labels": labels,
                   "qubit": qubit, "original_qubit": self.original_qubits.get(params.n),
                   "seed": self.settings.simulation_seed}
        return context, kwargs, axes

    def _execute(self, report, context, kwargs):
        with self.store.run_lock("quantumx-worker-" + report["experiment_id"]):
            self._execute_locked(report, context, kwargs)

    def _execute_locked(self, report, context, kwargs):
        experiment_id = report["experiment_id"]
        recorder = None
        try:
            report["status"] = "running"
            self._save(report)
            recorder = RecordingBackend(self.backend_factory(context), self.store, report["artifact_directory"],
                                        context["labels"], stop_event=_StopFlag(self, experiment_id), expected_shots=kwargs["shots"])
            result = getattr(self.library, report["function"])(backend=recorder, **kwargs)
            if len(recorder.records) != len(context["labels"]):
                raise ValueError("Function acquisition count differs from declared plan")
            warnings = []
            raw = json_ready(result, warnings)
            report["result_ref"] = self.store.write_artifact(report["artifact_directory"], "result.json", raw)
            metrics, quality, metric_warnings = summarize(report["experiment_type"], result)
            report.update(status="completed", metrics=metrics, quality=quality, warnings=warnings + metric_warnings)
        except Exception as exc:
            report.update(status="interrupted" if isinstance(exc, ExperimentInterrupted) else "failed",
                          error_type=type(exc).__name__, quality={"accepted": False, "independently_verified": False})
        finally:
            report["iq_records"] = recorder.records if recorder else []
            report["actual_measurements"] = sum(r["shots"] for r in report["iq_records"])
            report["finished_at"] = time.time()
            self._save(report)

    def is_active(self, experiment_id):
        worker = self._workers.get(experiment_id)
        return bool(worker and worker.is_alive())

    def wait(self, timeout=None):
        for worker in list(self._workers.values()):
            worker.join(timeout)

    def pause(self):
        self._pause.set()

    def stop(self, experiment_id):
        self.get(experiment_id)
        with self.store.connect() as db:
            db.execute("UPDATE quantumx_experiments SET stopped=1 WHERE id=?", (experiment_id,))
        # If no process owns the execution lock, acknowledge an abandoned task.
        if not self.is_active(experiment_id):
            try:
                with self.store.run_lock("quantumx-worker-" + experiment_id):
                    report = self.get(experiment_id)
                    if report["status"] in {"queued", "running"}:
                        manifest = self.store.root / report["artifact_directory"] / "acquisitions.json"
                        records = read_json_config(manifest).get("iq_records", []) if manifest.exists() else []
                        report.update(status="interrupted", iq_records=records,
                                      actual_measurements=sum(r["shots"] for r in records), finished_at=time.time())
                        self._save(report)
            except RunBusy:
                pass
        return self.public_result(experiment_id)

    def compare(self, first, second):
        a, b = self.get(first), self.get(second)
        if a["n"] != b["n"] or a["experiment_type"] != b["experiment_type"]:
            raise ValueError("只能比较同一目标、同一种实验")
        if a["status"] != "completed" or b["status"] != "completed":
            raise ValueError("只能比较已完成的实验")
        delta = {k: b["metrics"][k] - v for k, v in a["metrics"].items()
                 if type(v) in (float, int) and type(b["metrics"].get(k)) in (float, int)}
        return {"first": self.public_result(first), "second": self.public_result(second), "metric_delta_second_minus_first": delta}

    def run_sync(self, name, parameters=None, session_id=None):
        receipt = self.start(name, parameters or {}, session_id or uuid4().hex, uuid4().hex)
        self.wait()
        return self.public_result(receipt["experiment_id"])


class _StopFlag:
    def __init__(self, service, experiment_id):
        self.service, self.experiment_id = service, experiment_id

    def is_set(self):
        if self.service._pause.is_set():
            return True
        with self.service.store.connect() as db:
            return bool(db.execute("SELECT stopped FROM quantumx_experiments WHERE id=?", (self.experiment_id,)).fetchone()[0])
