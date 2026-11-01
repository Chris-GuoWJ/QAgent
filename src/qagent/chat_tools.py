"""Narrow chat operations; no filesystem, shell, or adapter-private tools."""

import hashlib
import threading

from langchain_core.tools import tool
from pydantic import Field

from .chat_history import ChatHistory
from .schemas import CalibrationTask, Model
from .storage import RunBusy


class StartArguments(Model):
    qubit: str = Field(description="Target qubit; must match the configured task profile")
    lower_hz: float | None = Field(default=None, description="Optional lower frequency in Hz, inside the approved range")
    upper_hz: float | None = Field(default=None, description="Optional upper frequency in Hz, inside the approved range")
    max_experiments: int | None = Field(default=None, description="Optional experiment limit, no greater than approved limit")
    max_measurements: int | None = Field(default=None, description="Optional measurement limit, no greater than approved limit")
    target_uncertainty_hz: float | None = Field(default=None, description="Optional tighter target uncertainty in Hz")
    initial_points: int | None = Field(default=None, description="Optional initial number of frequency points")
    initial_shots: int | None = Field(default=None, description="Optional initial shots per frequency point")


class RunArguments(Model):
    run_id: str | None = Field(default=None, description="Omit to use the selected task in this conversation")


class ChatOperations:
    def __init__(self, controller, profile: CalibrationTask, history: ChatHistory, poll_interval=0.05,
                 metadata=None, on_event=None, worker=None):
        self.controller, self.profile, self.history = controller, profile, history
        self.store = controller.store
        self.poll_interval = poll_interval
        self.metadata = metadata or {}
        self.on_event = on_event or (lambda event: None)
        self._worker_function = worker
        self._workers = {}
        self._pause = threading.Event()
        self._started_this_turn = False
        self.quantumx = getattr(controller, "quantumx_service", None)
        if self.quantumx is not None:
            self.quantumx.bind_session(history.session_id)

    def begin_turn(self):
        self._started_this_turn = False

    def task_info(self):
        result = {"task_profile": self.profile.model_dump(),
                "capabilities": self.controller.capabilities.model_dump(),
                "selected_run_id": self.history.selected_run,
                "note": "Use the configured profile defaults unless the user requests tighter limits. Never read configuration files."}
        if self.quantumx is not None:
            result["quantumx"] = self.quantumx.describe()
        return result

    def _resolve(self, run_id):
        selected = run_id or self.history.selected_run
        if not selected:
            raise ValueError("尚未选中任务，请先列出任务并指定一个运行编号，或发起校准")
        state = self.store.load(selected)
        if state.backend_identity != self.controller.backend.identity:
            raise ValueError("该任务属于不同实验后端，请使用其原运行配置")
        self.history.select(selected)
        return selected

    def summary(self, run_id=None):
        run_id = self._resolve(run_id)
        state = self.store.load(run_id)
        candidate = next((a for a in state.analyses if a.analysis_id == state.candidate_id), None)
        return {"run_id": run_id, "qubit": state.task.qubit, "stage": state.stage,
                "measurement_type": self.controller.capabilities.data_type,
                "response_mode": self.controller.capabilities.response_mode,
                "outcome": state.outcome, "reason": state.reason or state.feedback,
                "stop_requested": self.store.stopped(run_id),
                "executor_active_here": self.is_active(run_id),
                "budget": {"experiments": state.experiments, "max_experiments": state.task.max_experiments,
                    "used_measurements": state.used_measurements, "reserved_measurements": state.reserved_measurements,
                    "max_measurements": state.task.max_measurements},
                "candidate": candidate.model_dump() if candidate else None,
                "latest_analysis": state.analyses[-1].model_dump() if state.analyses else None,
                "verification": [v.model_dump() for v in state.verifications[-3:]],
                "active_job_id": state.current.job_id if state.current else None}

    def runs(self):
        result = []
        for item in self.store.list_runs():
            state = self.store.load(item["run_id"])
            if state.backend_identity == self.controller.backend.identity:
                result.append({**item, "qubit": state.task.qubit})
            if len(result) >= 20:
                break
        return {"runs": result, "selected_run_id": self.history.selected_run}

    def _operation_key(self, call_id):
        return hashlib.sha256(f"{self.history.session_id}:{call_id}".encode()).hexdigest()

    def _find_operation(self, call_id):
        key = self._operation_key(call_id)
        for item in self.store.list_runs():
            state = self.store.load(item["run_id"])
            if state.metadata.get("chat_operation") == key:
                return state
        return None

    def start(self, call_id, **arguments):
        if self._pause.is_set():
            raise ValueError("会话正在退出，请在新会话中启动任务")
        arguments = StartArguments.model_validate(arguments)
        overrides = arguments.model_dump(exclude_none=True)
        values = self.profile.model_dump() | overrides
        task = CalibrationTask.model_validate(values)
        if task.qubit != self.profile.qubit:
            raise ValueError("当前任务约定只允许校准 " + self.profile.qubit)
        if not self.profile.lower_hz <= task.lower_hz < task.upper_hz <= self.profile.upper_hz:
            raise ValueError("请求的频率范围超出当前任务约定")
        if (task.max_experiments > self.profile.max_experiments
                or task.max_measurements > self.profile.max_measurements
                or task.target_uncertainty_hz > self.profile.target_uncertainty_hz):
            raise ValueError("不能扩大约定预算或放宽成功标准，可在约定内收紧参数")
        # Serialize task creation across sessions. Existing low-level CLI runs are
        # also checked before starting another task through chat.
        with self.store.run_lock("chat-task-creation"):
            existing = self._find_operation(call_id)
            if existing:
                if existing.task != task:
                    raise ValueError("同一工具调用编号不能用于不同任务")
                self.history.select(existing.run_id)
                return self.summary(existing.run_id)
            if self._started_this_turn:
                raise ValueError("本条指令已创建一个任务，请先查询它的结果")
            for item in self.store.list_runs():
                state = self.store.load(item["run_id"])
                if state.backend_identity == self.controller.backend.identity and state.stage != "finished":
                    raise ValueError("已有未结束或待核对任务，请先查询、恢复或停止它：" + state.run_id)
            metadata = self.metadata | {"chat_operation": self._operation_key(call_id)}
            state = self.controller.create(task, metadata)
            self.history.select(state.run_id)
            self._started_this_turn = True
        self._launch(state.run_id)
        return {"scheduled": True, **self.summary(state.run_id)}

    def is_active(self, run_id):
        worker = self._workers.get(run_id)
        return bool(worker and worker.is_alive())

    def _launch(self, run_id):
        if self.is_active(run_id):
            return

        def execute():
            try:
                from .graph import drive

                function = self._worker_function or drive
                report = function(self.controller, run_id, poll_interval=self.poll_interval,
                                  on_event=self.on_event, pause_event=self._pause)
                self.on_event({"event": "executor_returned", "run_id": run_id,
                               "stage": report["stage"], "outcome": report["outcome"]})
            except RunBusy:
                self.on_event({"event": "executor_busy", "run_id": run_id})
            except Exception as exc:
                self.on_event({"event": "executor_error", "run_id": run_id, "error_type": type(exc).__name__})

        worker = threading.Thread(target=execute, name="calibration-" + run_id[:8], daemon=True)
        self._workers[run_id] = worker
        worker.start()

    def resume(self, run_id=None):
        run_id = self._resolve(run_id)
        state = self.store.load(run_id)
        if state.stage == "finished":
            return self.summary(run_id)
        if state.policy_identity != self.controller.policy.identity:
            raise ValueError("该任务使用不同的实验决策配置，请使用原来的运行配置恢复")
        if self._pause.is_set():
            raise ValueError("会话正在退出，请在新会话中恢复任务")
        self._launch(run_id)
        return {"scheduled": True, **self.summary(run_id)}

    def stop(self, run_id=None):
        run_id = self._resolve(run_id)
        if self.store.load(run_id).stage == "finished":
            return {"message": "任务已经结束，未发起新的停止或取消操作", **self.summary(run_id)}
        self.store.request_stop(run_id)
        # An inactive run still needs a control transition to acknowledge stop.
        if self.store.load(run_id).policy_identity == self.controller.policy.identity:
            self._launch(run_id)
        return {"message": "已记录停止请求；已经在途的实验不会被隐式取消", **self.summary(run_id)}

    def events(self, run_id=None):
        run_id = self._resolve(run_id)
        return {"run_id": run_id, "events": self.store.events(run_id)[-12:]}

    def interrupted_operation(self, name, call_id):
        """Repair chat protocol without blindly replaying a lost write receipt."""
        existing = self._find_operation(call_id) if name == "start_calibration" else None
        if existing:
            self.history.select(existing.run_id)
            return {"recovered_receipt": True, **self.summary(existing.run_id)}
        return {"interrupted": True, "selected_run_id": self.history.selected_run,
                "message": "上次操作的对话回执不完整，未自动重放。请先查询已有任务，再根据用户最新指令处理。"}

    def wait(self, timeout=None):
        for worker in list(self._workers.values()):
            worker.join(timeout)
        if self.quantumx is not None:
            self.quantumx.wait(timeout)

    @property
    def closing(self):
        return self._pause.is_set()

    def pause(self):
        self._pause.set()
        if self.quantumx is not None:
            self.quantumx.pause()

    def close(self):
        self.pause()
        self.wait(timeout=2)


def create_tools(operations: ChatOperations, call_id):
    """call_id is injected by the chat loop, never generated by the model as data."""

    @tool
    def describe_calibration() -> dict:
        """Get the configured experiment, authorized profile and backend capabilities."""
        return operations.task_info()

    @tool(args_schema=StartArguments)
    def start_calibration(**arguments) -> dict:
        """Start one requested calibration in the background using approved defaults or tighter overrides."""
        return operations.start(call_id(), **arguments)

    @tool(args_schema=RunArguments)
    def get_run_status(run_id=None) -> dict:
        """Read actual progress, budget, candidate and verification for a task; defaults to the selected task."""
        return operations.summary(run_id)

    @tool
    def list_calibrations() -> dict:
        """List existing calibration tasks for the current backend. Does not start or resume tasks."""
        return operations.runs()

    @tool(args_schema=RunArguments)
    def resume_calibration(run_id=None) -> dict:
        """Resume an existing unfinished task in the background, preserving its request and backend receipts."""
        return operations.resume(run_id)

    @tool(args_schema=RunArguments)
    def stop_calibration(run_id=None) -> dict:
        """Persist a stop request for the selected task. Does not claim that an in-flight device task is cancelled."""
        return operations.stop(run_id)

    @tool(args_schema=RunArguments)
    def read_calibration_report(run_id=None) -> dict:
        """Read actual results and independent verification evidence. A candidate alone is not calibration success."""
        return operations.summary(run_id)

    @tool(args_schema=RunArguments)
    def recent_calibration_events(run_id=None) -> dict:
        """Read the most recent persisted events for the selected experiment run."""
        return operations.events(run_id)

    return [describe_calibration, start_calibration, get_run_status, list_calibrations,
            resume_calibration, stop_calibration, read_calibration_report, recent_calibration_events]
