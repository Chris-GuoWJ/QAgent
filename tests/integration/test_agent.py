import importlib
import io
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from qagent.agent.loop import ChatAgent, ChatError
from qagent.agent.history import ChatHistory
from qagent.agent.session import ChatOperations
from qagent.interfaces.terminal import serve_terminal
from tests.helpers import SOURCE, make_service

pytestmark = pytest.mark.skipif(not (SOURCE / "src/quantumx/calibration.py").exists(), reason="Local QuantumX2 source unavailable")


class ScriptedModel:
    def __init__(self, responses):
        self.responses, self.inputs = list(responses), []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def call(name, args=None, identifier="call"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": identifier}])


def operations(tmp_path):
    service = make_service(tmp_path)
    return ChatOperations(service, ChatHistory(service.store, service.identity))


def test_hardware_tools_only_and_background_execution(tmp_path):
    ops = operations(tmp_path)
    model = ScriptedModel([call("measure_readout_centers", {"n": 1}), AIMessage(content="已调度读出中心实验。")])
    agent = ChatAgent(model, ops)
    assert "start_calibration" not in agent.tools and "tune_frequency_ramsey" in agent.tools
    assert "调度" in agent.respond("计算 q1 的读出中心")
    ops.wait()
    assert ops.quantumx.list_results()["experiments"][0]["status"] == "completed"
    assert any(isinstance(m, HumanMessage) for m in model.inputs[-1])
    assert any(isinstance(m, ToolMessage) for m in model.inputs[-1])
    ops.close()


def test_multiple_experiments_in_one_instruction_are_rejected(tmp_path):
    ops = operations(tmp_path)
    message = AIMessage(content="", tool_calls=[
        {"name": "measure_readout_centers", "args": {"n": 1}, "id": "one"},
        {"name": "tune_pi_amplitude", "args": {"n": 1}, "id": "two"}])
    agent = ChatAgent(ScriptedModel([message, AIMessage(content="已创建一个实验。")]), ops)
    agent.respond("校准 q1")
    ops.wait()
    replies = [json.loads(m.content) for m in ops.history.messages() if isinstance(m, ToolMessage)]
    assert "本条指令" in replies[1]["error"]
    assert len(ops.quantumx.list_results()["experiments"]) == 1
    ops.close()


def test_missing_tool_receipt_never_replays_experiment(tmp_path):
    ops = operations(tmp_path)
    receipt = ops.quantumx.start("readout_centers", {}, ops.history.session_id, "existing")
    ops.wait()
    agent = ChatAgent(ScriptedModel([]), ops)
    for identifier in ("existing", "unknown"):
        ops.history.append(call("measure_readout_centers", {"n": 1}, identifier))
        agent._repair_pending_calls()
    replies = [json.loads(m.content) for m in ops.history.messages() if isinstance(m, ToolMessage)]
    assert replies[0]["experiment_id"] == receipt["experiment_id"] and replies[1]["interrupted"]
    assert len(ops.quantumx.list_results()["experiments"]) == 1


def test_model_failure_preserves_scheduled_experiment(tmp_path):
    ops = operations(tmp_path)
    agent = ChatAgent(ScriptedModel([call("measure_readout_centers"), RuntimeError("PRIVATE_SDK_BODY")]), ops)
    with pytest.raises(ChatError) as error:
        agent.respond("计算 q1 读出中心")
    ops.wait()
    assert "PRIVATE_SDK_BODY" not in str(error.value)
    assert len(ops.quantumx.list_results()["experiments"]) == 1
    ops.close()


def test_queries_never_start_experiments_and_history_binding_is_checked(tmp_path):
    ops = operations(tmp_path)
    model = ScriptedModel([call("describe_quantumx_experiments"), AIMessage(content="支持三种实验。")])
    assert "三种" in ChatAgent(model, ops).respond("可以做什么？")
    assert not ops.quantumx.list_results()["experiments"]
    with pytest.raises(ValueError, match="变化"):
        ChatHistory(ops.store, "changed", ops.history.session_id)


def test_pipe_terminal_shows_hardware_targets_and_result_path(tmp_path, monkeypatch):
    ops = operations(tmp_path)
    agent = ChatAgent(ScriptedModel([AIMessage(content="可计算读出中心。")]), ops)
    monkeypatch.setattr("sys.stdin", io.StringIO("可以做什么？\n/exit\n"))
    output = []
    assert serve_terminal(agent, write=output.append) == 0
    assert any("真机实验终端" in line for line in output)
    assert any(str(tmp_path / "result") in line for line in output)
    assert all("Q0" not in line for line in output)
    assert ops.closing


def test_real_sdk_tool_protocol_with_mock_http(tmp_path):
    from langchain_deepseek import ChatDeepSeek
    from openai import DefaultHttpxClient

    transport_module = next(cls.__module__.split(".")[0] for cls in DefaultHttpxClient.__mro__
                            if cls.__module__.split(".")[0] in {"httpx", "httpx2"})
    httpx = importlib.import_module(transport_module)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            names = {t["function"]["name"] for t in body["tools"]}
            assert "describe_quantumx_experiments" in names and "start_calibration" not in names
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "http", "type": "function", "function": {"name": "describe_quantumx_experiments", "arguments": "{}"}}]}
            finish = "tool_calls"
        else:
            assert any(m["role"] == "tool" for m in body["messages"])
            message, finish = {"role": "assistant", "content": "支持 QuantumX 真机实验。"}, "stop"
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
            "model": "fixture", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        model = ChatDeepSeek(model="fixture", api_key="fake-key", api_base="https://example.invalid",
                             http_client=client, max_retries=0)
        ops = operations(tmp_path)
        assert "QuantumX" in ChatAgent(model, ops).respond("你能做什么？")
    assert len(requests) == 2 and not ops.quantumx.list_results()["experiments"]
