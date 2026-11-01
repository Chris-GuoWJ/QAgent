import json
import pytest

from qagent.app import create_service, create_store, load_runtime
from qagent.interfaces.cli import main
from tests.helpers import SOURCE

@pytest.mark.skipif(not (SOURCE / "src/quantumx/calibration.py").exists(), reason="Local QuantumX2 source unavailable")
def test_runtime_relative_paths_check_and_parameter_files(tmp_path, capsys, monkeypatch):
    import qagent.core.runner as module

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
