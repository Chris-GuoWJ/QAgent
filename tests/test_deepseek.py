import json

import pytest

from qagent.policies.deepseek import DeepSeekPolicy, DeepSeekSettings
from qagent.schemas import Decision
from qagent.state import build_agent_view


class RecordingRunnable:
    def __init__(self, response):
        self.response, self.messages = response, None

    def invoke(self, messages):
        self.messages = messages
        return self.response


def test_deepseek_decision_adapter_validates_and_projects_context(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    runnable = RecordingRunnable({"parsed": Decision(action="finish", reason="No further measurements"),
                                  "parsing_error": None})
    policy = DeepSeekPolicy(DeepSeekSettings(model="test-model"), runnable=runnable)
    result = policy.choose(build_agent_view(state, controller.capabilities))
    assert result.action == "finish"
    serialized = json.dumps(runnable.messages)
    assert "true_frequency" not in serialized and "noise_std" not in serialized
    assert "api_key" not in serialized and "seed" not in serialized


@pytest.mark.parametrize("response", [
    {"parsed": None, "parsing_error": "bad JSON"},
    {"action": "execute_shell", "reason": "not allowed"},
    {"action": "scan", "reason": "missing configuration"},
])
def test_deepseek_rejects_invalid_model_outputs(make_controller, task, response):
    controller = make_controller()
    state = controller.create(task)
    policy = DeepSeekPolicy(DeepSeekSettings(model="test"), runnable=RecordingRunnable(response))
    with pytest.raises(ValueError):
        policy.choose(build_agent_view(state, controller.capabilities))


@pytest.mark.parametrize("method", ["function_calling", "json_mode"])
def test_deepseek_instantiation_without_network(monkeypatch, method):
    # Uses the actual LangChain integration but never invokes the HTTP client.
    import langchain_deepseek

    original = langchain_deepseek.ChatDeepSeek
    clients = []

    def record(**kwargs):
        client = original(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(langchain_deepseek, "ChatDeepSeek", record)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-local-construction-test")
    policy = DeepSeekPolicy(DeepSeekSettings(model="test-model", base_url="https://example.invalid", output_method=method))
    assert policy.identity.startswith("deepseek:")
    assert str(clients[0].api_base).rstrip("/") == "https://example.invalid"
    assert clients[0].max_retries == 0


def test_deepseek_missing_credentials_is_clear(monkeypatch):
    monkeypatch.delenv("QAGENT_TEST_MISSING_KEY", raising=False)
    with pytest.raises(ValueError, match="QAGENT_TEST_MISSING_KEY"):
        DeepSeekPolicy(DeepSeekSettings(model="test", api_key_env="QAGENT_TEST_MISSING_KEY"))
