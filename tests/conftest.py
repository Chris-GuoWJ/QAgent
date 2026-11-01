import pytest


@pytest.fixture(autouse=True)
def protect_user_credentials(monkeypatch):
    monkeypatch.setenv("QAGENT_NO_ENV_FILE", "1")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
