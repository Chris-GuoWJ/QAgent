from pathlib import Path

import pytest

from qagent.interfaces.cli import main
from qagent.core.config import read_json_config
from qagent.core.storage import Store


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.example", "production.env", ".ENV", "PRODUCTION.ENV"])
def test_input_guard_never_reads_credentials(tmp_path, monkeypatch, name):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not read")
    monkeypatch.setattr(Path, "read_text", forbidden)
    with pytest.raises(ValueError, match="禁止"):
        read_json_config(tmp_path / name)


def test_artifact_paths_cannot_escape_output_roots(tmp_path):
    store = Store(tmp_path / "runs")
    with pytest.raises(ValueError):
        store.write_artifact("../escape", "data.json", {})
    with pytest.raises(ValueError):
        store.read_input("../../outside.json")


def test_default_cli_launches_hardware_chat_without_extra_arguments(monkeypatch):
    import qagent.interfaces.terminal
    calls = []
    monkeypatch.setattr(qagent.interfaces.terminal, "launch_chat", lambda *args: calls.append(args) or 0)
    assert main([]) == 0
    assert calls[0][1].name == "runtime.json" and calls[0][1].parent.name == "config"


@pytest.mark.parametrize("obsolete", ["start", "resume", "quantumx", "demo"])
def test_obsolete_demo_commands_removed(obsolete):
    with pytest.raises(SystemExit) as error:
        main([obsolete])
    assert error.value.code == 2


def test_missing_runtime_and_missing_hardware_are_clear_errors(tmp_path, capsys):
    assert main(["check", "--runtime", str(tmp_path / "missing.json")]) == 2
    assert "找不到运行配置" in capsys.readouterr().err
