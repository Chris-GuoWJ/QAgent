from pathlib import Path

import pytest

from qagent.bootstrap import RuntimeSettings, deepseek_settings
from qagent.credentials import load_local_credentials


def test_application_loads_only_allowlisted_values_without_reading_files(tmp_path):
    calls = []
    environment = {}

    def fake_loader(**kwargs):
        calls.append(kwargs)
        return {"DEEPSEEK_MODEL": "fixture-model", "DEEPSEEK_API_KEY": "fixture-credential",
                "DEEPSEEK_BASE_URL": "https://example.invalid", "PATH": "must-not-export",
                "UNRELATED_SECRET": "must-not-export"}

    load_local_credentials(tmp_path, environ=environment, loader=fake_loader)
    assert environment == {"DEEPSEEK_MODEL": "fixture-model", "DEEPSEEK_API_KEY": "fixture-credential",
                           "DEEPSEEK_BASE_URL": "https://example.invalid"}
    assert calls == [{"dotenv_path": tmp_path / ".env", "encoding": "utf-8-sig",
                      "interpolate": False, "verbose": False}]


def test_existing_process_environment_takes_precedence():
    environment = {"DEEPSEEK_MODEL": "process-model", "DEEPSEEK_API_KEY": "process-key"}
    load_local_credentials(environ=environment, loader=lambda **kwargs: {
        "DEEPSEEK_MODEL": "file-model", "DEEPSEEK_API_KEY": "file-key"})
    assert environment == {"DEEPSEEK_MODEL": "process-model", "DEEPSEEK_API_KEY": "process-key"}


def test_blank_process_values_can_be_filled_and_empty_file_values_are_ignored():
    environment = {"DEEPSEEK_MODEL": ""}
    load_local_credentials(environ=environment, loader=lambda **kwargs: {
        "DEEPSEEK_MODEL": " fixture ", "DEEPSEEK_API_KEY": None, "DEEPSEEK_BASE_URL": " "})
    assert environment == {"DEEPSEEK_MODEL": "fixture"}


def test_configured_custom_key_variable_is_supported():
    environment = {}
    load_local_credentials(environ=environment, extra_keys=("LAB_MODEL_KEY",),
                           loader=lambda **kwargs: {"LAB_MODEL_KEY": "fixture-custom-key"})
    assert environment == {"LAB_MODEL_KEY": "fixture-custom-key"}


def test_development_switch_prevents_any_loader_or_file_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No credential file may be read")

    monkeypatch.setattr(Path, "read_text", forbidden)
    load_local_credentials(environ={"QAGENT_NO_ENV_FILE": "1"}, loader=forbidden)


def test_loader_error_does_not_expose_credential_content():
    def broken(**kwargs):
        raise RuntimeError("PRIVATE_CONTENT")

    with pytest.raises(ValueError) as error:
        load_local_credentials(environ={}, loader=broken)
    assert "PRIVATE_CONTENT" not in str(error.value)
    assert "RuntimeError" in str(error.value)


def test_cli_model_settings_bootstrap_uses_injected_loader(monkeypatch):
    import qagent.credentials as credentials
    import os

    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    calls = []

    def fake_bootstrap(**kwargs):
        calls.append(kwargs)
        monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-from-startup")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")

    monkeypatch.setattr(credentials, "load_local_credentials", fake_bootstrap)
    result = deepseek_settings(RuntimeSettings())
    assert result.model == "fixture-from-startup"
    assert result.base_url == "https://example.invalid"
    assert calls == [{"extra_keys": ("DEEPSEEK_API_KEY",)}]


def test_runtime_json_still_overrides_environment_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "process-model")
    settings = deepseek_settings(RuntimeSettings(deepseek={"model": "json-model"}))
    assert settings.model == "json-model"
