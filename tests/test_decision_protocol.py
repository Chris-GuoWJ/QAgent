"""Decision requests exercise the real SDK against an in-memory HTTP service."""

import importlib
import json

import pytest

from conftest import finish
from qagent.policies.deepseek import DeepSeekPolicy, DeepSeekSettings
from qagent.policy import FixedPolicy
from qagent.state import AgentView, build_agent_view


@pytest.fixture
def decision_service(monkeypatch):
    from langchain_deepseek import ChatDeepSeek
    from openai import DefaultHttpxClient

    transport_module = next(cls.__module__.split(".")[0] for cls in DefaultHttpxClient.__mro__
                            if cls.__module__.split(".")[0] in {"httpx", "httpx2"})
    httpx = importlib.import_module(transport_module)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-offline-decision-key")
    requests = []
    replies = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        # Thinking endpoints and some compatible gateways reject forced calls.
        choice = body.get("tool_choice")
        if choice is not None and choice != "auto":
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                "message": "Forced tool_choice is not supported in thinking mode"}})
        if replies:
            message = replies.pop(0)
            if "_http_status" in message:
                return httpx.Response(message["_http_status"], json={"error": {
                    "type": "invalid_request_error", "message": "PRIVATE_API_RESPONSE dummy-offline-decision-key"}})
        else:
            view = AgentView.model_validate_json(body["messages"][-1]["content"])
            decision = FixedPolicy().choose(view)
            if body.get("response_format") == {"type": "json_object"}:
                message = {"role": "assistant", "content": decision.model_dump_json()}
            else:
                message = {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"decision-{len(requests)}", "type": "function", "function": {
                        "name": "Decision", "arguments": decision.model_dump_json()}}]}
        return httpx.Response(200, json={"id": "decision-fixture", "object": "chat.completion", "created": 1,
            "model": body["model"], "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        def model_factory(**kwargs):
            return ChatDeepSeek(**kwargs, http_client=client)

        monkeypatch.setattr("langchain_deepseek.ChatDeepSeek", model_factory)
        yield requests, replies


@pytest.mark.parametrize("thinking", [None, "enabled", "disabled"])
def test_decision_request_uses_auto_tool_choice(make_controller, task, decision_service, thinking):
    requests, _ = decision_service
    controller = make_controller()
    state = controller.create(task)
    policy = DeepSeekPolicy(DeepSeekSettings(model="mock-model", base_url="https://example.invalid", thinking=thinking))
    decision = policy.choose(build_agent_view(state, controller.capabilities))
    assert decision.action == "scan"
    assert requests[0]["tool_choice"] == "auto"
    assert requests[0]["tools"][0]["function"]["name"] == "Decision"
    assert "Call the Decision tool exactly once" in requests[0]["messages"][0]["content"]
    if thinking is None:
        assert "thinking" not in requests[0]
    else:
        assert requests[0]["thinking"] == {"type": thinking}


def test_json_decision_request_does_not_bind_tools(make_controller, task, decision_service):
    requests, _ = decision_service
    controller = make_controller()
    state = controller.create(task)
    policy = DeepSeekPolicy(DeepSeekSettings(model="mock-model", base_url="https://example.invalid", output_method="json_mode"))
    assert policy.choose(build_agent_view(state, controller.capabilities)).action == "scan"
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in requests[0] and "tool_choice" not in requests[0]


@pytest.mark.parametrize("message", [
    {"role": "assistant", "content": "Calibration succeeded without any measurement"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "bad-decision", "type": "function", "function": {
            "name": "Decision", "arguments": '{"action":"scan","reason":"missing parameters"}'}}]},
])
def test_auto_tool_choice_still_requires_a_valid_decision(make_controller, task, decision_service, message):
    _, replies = decision_service
    replies.append(message)
    controller = make_controller()
    state = controller.create(task)
    policy = DeepSeekPolicy(DeepSeekSettings(model="mock-model", base_url="https://example.invalid"))
    with pytest.raises(ValueError, match="decision schema"):
        policy.choose(build_agent_view(state, controller.capabilities))
    assert not controller.backend.lookup("bad-decision").job_id


def test_decision_protocol_completes_independent_verification(make_controller, task, decision_service):
    requests, _ = decision_service
    policy = DeepSeekPolicy(DeepSeekSettings(model="mock-model", base_url="https://example.invalid", thinking="enabled"))
    controller = make_controller(policy=policy)
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"
    assert state.verifications[-1].passed and state.experiments >= 3
    assert len(requests) == len(state.decisions)
    assert all(request["tool_choice"] == "auto" for request in requests)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, "PRIVATE_STATUS"])
def test_provider_error_exposes_only_numeric_status(make_controller, task, status):
    class ProviderError(RuntimeError):
        status_code = status

    class BrokenPolicy:
        identity = "broken-provider-test"

        def choose(self, view):
            raise ProviderError("PRIVATE_API_RESPONSE dummy-offline-decision-key")

    controller = make_controller(policy=BrokenPolicy())
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed" and state.experiments == state.used_measurements == 0
    recorded = state.model_dump_json() + json.dumps(controller.store.events(state.run_id))
    assert "PRIVATE_API_RESPONSE" not in recorded and "dummy-offline-decision-key" not in recorded
    assert "PRIVATE_STATUS" not in recorded
    if isinstance(status, int):
        assert f"HTTP {status}" in state.reason


def test_terminal_displays_the_recorded_failure_reason():
    from qagent.terminal import format_event

    reason = "Decision provider failed: ProviderError (HTTP 400); error limit reached"
    assert reason in format_event({"event": "run_finished", "outcome": "failed", "reason": reason})


def test_sdk_wrapped_request_error_keeps_status_without_body(make_controller, task, decision_service):
    _, replies = decision_service
    replies.extend([{"_http_status": 400}] * task.max_errors)
    policy = DeepSeekPolicy(DeepSeekSettings(model="mock-model", base_url="https://example.invalid"))
    controller = make_controller(policy=policy)
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed" and state.used_measurements == state.experiments == 0
    assert "OpenAIInvalidRequestError" in state.reason and "HTTP 400" in state.reason
    report = json.dumps(controller.report(state.run_id))
    assert "PRIVATE_API_RESPONSE" not in report and "dummy-offline-decision-key" not in report
