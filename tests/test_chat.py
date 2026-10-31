import hashlib
import io
import json
import threading

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from qagent.chat import ChatAgent, ChatError
from qagent.chat_history import ChatHistory
from qagent.chat_tools import ChatOperations
from qagent.config import read_json_config
from qagent.graph import drive
from qagent.terminal import format_event, serve_terminal


class ScriptedModel:
    """Explicit model-response fixture, not a natural-language intent evaluator."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def call(name, args=None, identifier="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": identifier, "type": "tool_call"}])


def make_operations(controller, task, session_id=None, on_event=None, worker=None):
    context = hashlib.sha256((controller.backend.identity + task.model_dump_json()).encode()).hexdigest()
    history = ChatHistory(controller.store, context, session_id)
    return ChatOperations(controller, task, history, poll_interval=0,
                          on_event=on_event, worker=worker)


def test_natural_language_turn_calls_tools_and_keeps_context(make_controller, task):
    operations = make_operations(make_controller(), task)
    model = ScriptedModel([call("describe_calibration"), AIMessage(content="可以校准 Q0 的共振频率。"),
                           call("list_calibrations", identifier="call-2"), AIMessage(content="目前还没有运行任务。")])
    agent = ChatAgent(model, operations)
    assert "Q0" in agent.respond("你能帮我做哪些实验？")
    assert "没有" in agent.respond("现在有没有任务？")
    assert any(isinstance(m, HumanMessage) and "哪些实验" in m.content for m in model.inputs[-1])
    assert any(isinstance(m, ToolMessage) for m in model.inputs[-1])
    assert not operations.store.list_runs()


def test_chat_starts_real_control_loop_in_background(make_controller, task):
    events = []
    operations = make_operations(make_controller(), task, on_event=events.append)
    model = ScriptedModel([call("start_calibration", {"qubit": "Q0"}), AIMessage(content="已调度校准任务。")])
    agent = ChatAgent(model, operations)
    try:
        assert "已调度" in agent.respond("请帮我校准 Q0 的共振频率")
        operations.wait(timeout=20)
        summary = operations.summary()
        assert summary["outcome"] == "success"
        assert summary["verification"][-1]["passed"]
        assert any(e["event"] == "analysis_completed" for e in events)
    finally:
        operations.close()


def test_start_is_idempotent_for_same_tool_call(make_controller, task):
    controller = make_controller()
    operations = make_operations(controller, task)
    try:
        first = operations.start("stable-call", qubit="Q0")
        operations.wait(timeout=20)
        second = operations.start("stable-call", qubit="Q0")
        assert first["run_id"] == second["run_id"]
        assert len(controller.store.list_runs()) == 1
        with pytest.raises(ValueError, match="不同任务"):
            operations.start("stable-call", qubit="Q0", max_experiments=1)
    finally:
        operations.close()


def test_model_cannot_start_multiple_tasks_in_one_turn(make_controller, task):
    operations = make_operations(make_controller(), task)
    try:
        operations.start("first", qubit="Q0")
        operations.wait(timeout=20)
        with pytest.raises(ValueError, match="本条指令"):
            operations.start("second", qubit="Q0")
    finally:
        operations.close()


@pytest.mark.parametrize("arguments", [
    {"qubit": "Q1"}, {"qubit": "Q0", "lower_hz": 4e9},
    {"qubit": "Q0", "max_experiments": 99},
    {"qubit": "Q0", "max_measurements": 99999999},
    {"qubit": "Q0", "target_uncertainty_hz": 9e9},
])
def test_chat_tools_enforce_profile_before_creating_run(make_controller, task, arguments):
    operations = make_operations(make_controller(), task)
    with pytest.raises(ValueError):
        operations.start("invalid", **arguments)
    assert not operations.store.list_runs()


def test_existing_pending_run_blocks_duplicate_start(make_controller, task):
    controller = make_controller()
    existing = controller.create(task)
    operations = make_operations(controller, task)
    with pytest.raises(ValueError, match=existing.run_id):
        operations.start("new", qubit="Q0")
    assert len(controller.store.list_runs()) == 1


def test_status_and_stop_work_while_worker_is_active(make_controller, task):
    entered = threading.Event()
    release = threading.Event()

    def delayed(controller, run_id, **kwargs):
        entered.set()
        assert release.wait(10)
        return drive(controller, run_id, **kwargs)

    operations = make_operations(make_controller(), task, worker=delayed)
    try:
        operations.start("start", qubit="Q0")
        assert entered.wait(5)
        assert operations.summary()["executor_active_here"]
        stopped = operations.stop()
        assert stopped["stop_requested"]
        release.set()
        operations.wait(timeout=20)
        assert operations.summary()["outcome"] == "stopped"
        assert operations.summary()["budget"]["used_measurements"] == 0
    finally:
        release.set()
        operations.close()


def test_conversation_and_selected_run_survive_reopening(make_controller, task):
    controller = make_controller()
    operations = make_operations(controller, task)
    run_id = controller.create(task).run_id
    operations.history.select(run_id)
    agent = ChatAgent(ScriptedModel([AIMessage(content="已记住当前任务。")]), operations)
    agent.respond("请记住当前任务")
    restored = make_operations(controller, task, session_id=operations.history.session_id)
    model = ScriptedModel([call("get_run_status"), AIMessage(content="任务还没有提交实验。")])
    reply = ChatAgent(model, restored).respond("它现在怎么样？")
    assert "没有提交" in reply
    assert restored.history.selected_run == run_id
    assert any(isinstance(m, HumanMessage) and "记住" in m.content for m in model.inputs[0])


def test_lost_start_receipt_is_recovered_without_creating_another_run(make_controller, task):
    operations = make_operations(make_controller(), task)
    try:
        created = operations.start("lost-call", qubit="Q0")
        operations.wait(timeout=20)
        operations.history.append(HumanMessage(content="开始校准 Q0"))
        operations.history.append(call("start_calibration", {"qubit": "Q0"}, "lost-call"))
        restored = make_operations(operations.controller, task, operations.history.session_id)
        agent = ChatAgent(ScriptedModel([AIMessage(content="找到了原任务的实际记录。")]), restored)
        agent.respond("上次那个任务怎么样了？")
        assert len(operations.store.list_runs()) == 1
        results = [json.loads(m.content) for m in restored.history.messages() if isinstance(m, ToolMessage)]
        assert results[0]["run_id"] == created["run_id"]
        assert results[0]["recovered_receipt"]
    finally:
        operations.close()


def test_pending_start_is_not_blindly_replayed_on_resume(make_controller, task):
    operations = make_operations(make_controller(), task)
    operations.history.append(HumanMessage(content="开始实验"))
    operations.history.append(call("start_calibration", {"qubit": "Q0"}))
    agent = ChatAgent(ScriptedModel([AIMessage(content="上次指令中断，还没有创建实验。")]), operations)
    agent.respond("先不要执行了")
    assert not operations.store.list_runs()
    result = next(m for m in operations.history.messages() if isinstance(m, ToolMessage))
    assert json.loads(result.content)["interrupted"]


def test_tools_and_prompt_exclude_filesystem_and_private_settings(make_controller, task):
    operations = make_operations(make_controller(), task)
    model = ScriptedModel([AIMessage(content="可以解释实验，但不能读取配置文件。")])
    agent = ChatAgent(model, operations)
    agent.respond("说明一下当前任务")
    names = {tool.name for tool in model.tools}
    assert not names & {"read_file", "write_file", "execute", "shell"}
    context = " ".join(str(m.content) for m in model.inputs[0])
    assert "true_frequency" not in context and "noise_std" not in context
    assert "seed" not in context


def test_unknown_tool_is_reported_without_execution(make_controller, task):
    operations = make_operations(make_controller(), task)
    model = ScriptedModel([call("read_file", {"path": ".env"}), AIMessage(content="没有读取文件的工具。")])
    agent = ChatAgent(model, operations)
    agent.respond("查看配置")
    result = next(m for m in operations.history.messages() if isinstance(m, ToolMessage))
    assert "不可用" in json.loads(result.content)["error"]
    assert not operations.store.list_runs()


def test_model_error_is_sanitized_and_history_is_recoverable(make_controller, task):
    operations = make_operations(make_controller(), task)
    agent = ChatAgent(ScriptedModel([ValueError("SECRET_RESPONSE")]), operations)
    with pytest.raises(ChatError) as error:
        agent.respond("查看任务")
    assert "SECRET_RESPONSE" not in str(error.value)
    assert len(operations.history.messages()) == 1


def test_model_tool_loop_is_bounded(make_controller, task):
    operations = make_operations(make_controller(), task)
    agent = ChatAgent(ScriptedModel([call("list_calibrations", identifier=str(i)) for i in range(3)]),
                      operations, max_tool_rounds=3)
    assert "上限" in agent.respond("列出任务")
    assert not operations.store.list_runs()


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.example", "production.env", ".ENV", "PRODUCTION.ENV"])
def test_configuration_guard_never_opens_environment_files(tmp_path, monkeypatch, name):
    from pathlib import Path

    def forbidden(*args, **kwargs):
        raise AssertionError("No file read was permitted in this test")

    monkeypatch.setattr(Path, "read_text", forbidden)
    with pytest.raises(ValueError, match="禁止读取"):
        read_json_config(tmp_path / name)


def test_default_cli_routes_to_conversation(monkeypatch, tmp_path):
    import qagent.terminal
    from qagent.cli import main

    calls = []
    monkeypatch.setattr(qagent.terminal, "launch_chat", lambda *args: calls.append(args) or 0)
    assert main(["--storage", str(tmp_path)]) == 0
    assert calls[0][0] == tmp_path
    assert main(["-n", "校准 Q0", "-r", "session-id"]) == 0
    assert calls[-1][-1] == "校准 Q0" and calls[-1][-2] == "session-id"
    assert main(["chat", "--message", "现在什么进度？"]) == 0
    assert calls[-1][-1] == "现在什么进度？"


def test_pipe_terminal_accepts_plain_natural_language(make_controller, task, monkeypatch):
    operations = make_operations(make_controller(), task)
    model = ScriptedModel([AIMessage(content="可以校准 Q0。"), AIMessage(content="目前没有任务。")])
    agent = ChatAgent(model, operations)
    monkeypatch.setattr("sys.stdin", io.StringIO("你能做什么？\n现在什么进度？\n/exit\n"))
    output = []
    assert serve_terminal(agent, write=output.append) == 0
    assert any("可以校准" in line for line in output)
    assert any("没有任务" in line for line in output)


def test_pause_signal_stops_executor_before_any_transition(make_controller, task):
    controller = make_controller()
    run_id = controller.create(task).run_id
    signal = threading.Event()
    signal.set()
    report = drive(controller, run_id, pause_event=signal)
    assert report["stage"] == "decide" and report["budget"]["experiments"] == 0


def test_progress_formatter_does_not_expose_internal_configuration():
    assert "待核对" in format_event({"event": "submission_unknown"})
    assert "正在运行" in format_event({"event": "job_progress", "status": "running"})


def test_real_langchain_tool_protocol_with_mock_http(make_controller, task, monkeypatch):
    """Exercise SDK serialization and tool-result round-trip without network access."""
    import importlib
    from openai import DefaultHttpxClient
    from langchain_deepseek import ChatDeepSeek

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    # SDK 2.x uses httpx; SDK 3.x uses httpx2. Select the actual public client's transport.
    transport_module = next(cls.__module__.split(".")[0] for cls in DefaultHttpxClient.__mro__
                            if cls.__module__.split(".")[0] in {"httpx", "httpx2"})
    httpx = importlib.import_module(transport_module)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert any(t["function"]["name"] == "describe_calibration" for t in body["tools"])
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "http-call", "type": "function", "function": {"name": "describe_calibration", "arguments": "{}"}}]}
            finish_reason = "tool_calls"
        else:
            assert any(m["role"] == "tool" and m["tool_call_id"] == "http-call" for m in body["messages"])
            message = {"role": "assistant", "content": "目前支持 Q0 频谱校准。"}
            finish_reason = "stop"
        return httpx.Response(200, json={"id": "chat-fixture", "object": "chat.completion", "created": 1,
            "model": "mock-model", "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        model = ChatDeepSeek(model="mock-model", api_key="dummy-no-network", api_base="https://example.invalid",
                             http_client=client, max_retries=0)
        operations = make_operations(make_controller(), task)
        assert "Q0" in ChatAgent(model, operations).respond("你可以做什么？")
    assert len(requests) == 2
    assert not operations.store.list_runs()


def test_one_shot_terminal_runs_job_and_prints_human_progress(make_controller, task):
    output = []
    operations = make_operations(make_controller(), task, on_event=lambda event: output.append(format_event(event)))
    agent = ChatAgent(ScriptedModel([call("start_calibration", {"qubit": "Q0"}),
                                    AIMessage(content="开始校准，测量结果会实时显示。")]), operations)
    assert serve_terminal(agent, message="校准 Q0", write=output.append) == 0
    assert operations.summary()["outcome"] == "success"
    assert any(text and "独立验证" in text for text in output)


def test_resuming_chat_with_changed_context_is_rejected(make_controller, task):
    operations = make_operations(make_controller(), task)
    with pytest.raises(ValueError, match="已变化"):
        ChatHistory(operations.store, "another-model-profile", operations.history.session_id)


def test_chat_preparation_loads_bundled_profile_without_environment_files(tmp_path, monkeypatch):
    from pathlib import Path
    import qagent.terminal as terminal

    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"policy": "fixed", "backend_options": {"duration_seconds": 0}}), encoding="utf-8")
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert not (path.name == ".env" or path.name.startswith(".env."))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    monkeypatch.setenv("DEEPSEEK_MODEL", "offline-fixture")
    model = ScriptedModel([AIMessage(content="支持 Q0 校准。")])
    monkeypatch.setattr(terminal, "create_chat_model", lambda settings: model)
    agent = terminal.prepare_chat(tmp_path / "runs", runtime_path=runtime)
    assert agent.operations.profile.qubit == "Q0"
    assert "Q0" in agent.respond("你支持哪些比特？")
    agent.operations.close()


def test_closing_during_model_response_prevents_late_task_creation(make_controller, task):
    operations = make_operations(make_controller(), task)

    class LateModel(ScriptedModel):
        def invoke(self, messages):
            operations.pause()
            return call("start_calibration", {"qubit": "Q0"})

    agent = ChatAgent(LateModel([]), operations)
    with pytest.raises(ChatError, match="未执行"):
        agent.respond("开始校准")
    assert not operations.store.list_runs()


def test_resume_requires_original_policy_but_stop_request_is_still_saved(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    state.policy_identity = "previous-policy"
    controller.store.save(state, "test_previous_policy")
    operations = make_operations(controller, task)
    with pytest.raises(ValueError, match="原来的运行配置"):
        operations.resume(state.run_id)
    stopped = operations.stop(state.run_id)
    assert stopped["stop_requested"] and not stopped["executor_active_here"]
    assert stopped["outcome"] is None


def test_natural_task_overrides_reach_the_controlled_experiment(make_controller, task):
    operations = make_operations(make_controller(), task)
    try:
        created = operations.start("configured-call", qubit="Q0", lower_hz=4.9e9, upper_hz=5.1e9,
            max_experiments=4, max_measurements=200000, initial_points=101, initial_shots=128)
        operations.wait(timeout=20)
        state = operations.store.load(created["run_id"])
        assert state.outcome == "success"
        assert state.requests[0].config.points == 101
        assert state.requests[0].config.shots_per_point == 128
        assert state.task.lower_hz == 4.9e9 and state.task.upper_hz == 5.1e9
    finally:
        operations.close()


def test_prompt_toolkit_interactive_loop_accepts_natural_language(make_controller, task):
    import asyncio
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from qagent.terminal import _interactive

    operations = make_operations(make_controller(), task)
    agent = ChatAgent(ScriptedModel([AIMessage(content="当前支持 Q0 频谱校准。")]), operations)
    output = []
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            pipe.send_text("你能做什么？\n/exit\n")
            asyncio.run(asyncio.wait_for(_interactive(agent, output.append), timeout=10))
    operations.close()
    assert output == ["Agent > 当前支持 Q0 频谱校准。"]


def test_stop_of_finished_task_does_not_claim_a_new_stop_request(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    controller._finish(state, "not_converged", "Task already ended")
    operations = make_operations(controller, task)
    count = len(controller.store.events(state.run_id))
    result = operations.stop(state.run_id)
    assert "已经结束" in result["message"]
    assert not result["stop_requested"]
    assert len(controller.store.events(state.run_id)) == count
