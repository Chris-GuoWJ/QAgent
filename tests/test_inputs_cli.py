import json
from pathlib import Path

import pytest

from qagent.bootstrap import create_service, create_store, load_runtime
from qagent.cli import main
from qagent.config import read_json_config
from qagent.storage import Store
from support import SOURCE


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
    import qagent.terminal
    calls = []
    monkeypatch.setattr(qagent.terminal, "launch_chat", lambda *args: calls.append(args) or 0)
    assert main([]) == 0
    assert calls[0][1].name == "runtime.json" and calls[0][1].parent.name == "config"


@pytest.mark.parametrize("obsolete", ["start", "resume", "quantumx", "demo"])
def test_obsolete_demo_commands_removed(obsolete):
    with pytest.raises(SystemExit) as error:
        main([obsolete])
    assert error.value.code == 2


@pytest.mark.skipif(not (SOURCE / "src/quantumx/calibration.py").exists(), reason="Local QuantumX2 source unavailable")
def test_runtime_relative_paths_check_and_parameter_files(tmp_path, capsys, monkeypatch):
    import qagent.integrations.quantumx.service as module

    config = tmp_path / "config"
    config.mkdir()
    (config / "params.json").write_text('{"n":2,"shots":100}', encoding="utf-8")
    path = config / "runtime.json"
    path.write_text(json.dumps({"result_dir": "../result", "input_archive_dir": "history", "quantumx": {
        "source_dir": str(SOURCE), "hardware_config": str(SOURCE / "configs/hardware_tmpl.json"),
        "qubits_config": str(SOURCE / "configs/qubits_tmpl.json"),
        "parameter_files": {"readout_centers": "params.json"}}}), encoding="utf-8")
    settings = load_runtime(path)
    assert settings.result_dir == tmp_path / "result"
    assert settings.input_archive_dir == config / "history"
    assert settings.quantumx.parameter_files["readout_centers"] == config / "params.json"
    monkeypatch.setattr(module, "load_backend_class", lambda *args: pytest.fail("check must not connect devices"))
    assert main(["--storage", str(tmp_path / "runs"), "check", "--runtime", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["configuration_valid"] and not result["device_connected"]
    service = create_service(create_store(tmp_path / "runs", settings), settings)
    assert service.describe()["experiments"][0]["defaults"]["n"] == 2


def test_missing_runtime_and_missing_hardware_are_clear_errors(tmp_path, capsys):
    assert main(["check", "--runtime", str(tmp_path / "missing.json")]) == 2
    assert "找不到运行配置" in capsys.readouterr().err
