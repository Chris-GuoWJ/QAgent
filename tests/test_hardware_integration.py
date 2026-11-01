"""Real QuantumX functions exercised through offline backend boundaries."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import threading

import numpy as np
from PIL import Image
import pytest
from pydantic import ValidationError

from qagent.integrations.quantumx.parameters import RabiParameters, RamseyParameters
from qagent.integrations.quantumx.recording_backend import RecordingBackend
from qagent.integrations.quantumx.results import json_ready
from support import FixtureIQBackend, SOURCE, make_service

pytestmark = pytest.mark.skipif(not (SOURCE / "src/quantumx/calibration.py").exists(), reason="Local QuantumX2 source unavailable")


def source_digest():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (SOURCE / "src", SOURCE / "configs") for p in root.rglob("*")
            if p.is_file() and p.suffix in {".py", ".pyc", ".json"}}


def test_original_functions_inputs_outputs_plots_and_candidates(tmp_path):
    before = source_digest()
    service = make_service(tmp_path)
    results, reference = [], None
    for name, count in (("readout_centers", 2), ("pi_amplitude", 41), ("ramsey_frequency", 62)):
        receipt = service.run_sync(name, {"reference_experiment_id": reference} if reference else {}, "chain")
        assert receipt["status"] == "completed" and receipt["quality"]["accepted"], receipt
        assert receipt["data_origin"] == "test" and not receipt.get("export_error_type")
        assert receipt["actual_measurements"] == receipt["reserved_measurements"] == count * 512
        output, inputs = Path(receipt["artifact_directory"]), Path(receipt["input_directory"])
        assert output.is_relative_to(tmp_path / "result")
        assert inputs.is_relative_to(tmp_path / "config")
        assert {"report.json", "analysis.json", "result.json", "summary.html", "iq.png", "acquisitions.json"} <= {p.name for p in output.iterdir()}
        assert {"parameters.json", "hardware.json", "qubits.json"} <= {p.name for p in inputs.iterdir()}
        page = (output / "summary.html").read_text(encoding="utf-8")
        assert "测试替身" in page and "尚未独立验证" in page
        report = service.get(receipt["experiment_id"])
        assert len(report["iq_records"]) == count
        for record in report["iq_records"]:
            raw = np.load(service.store.result_root / record["data_ref"], allow_pickle=False)
            text = np.loadtxt((service.store.result_root / record["data_ref"]).with_suffix(".csv"), delimiter=",", skiprows=1)
            assert raw.shape == (512, 2)
            np.testing.assert_array_equal(raw, text)
            assert service.store.read_artifact(record["pulse_ir_ref"])
        assert service.store.read_artifact(report["result_ref"])["updated"] is False
        with Image.open(output / "iq.png") as picture:
            picture.verify()
        if name != "readout_centers":
            assert (output / "curve.csv").exists()
            with Image.open(output / "fit.png") as picture:
                picture.verify()
        assert "iq0" not in receipt["metrics"]
        reference = receipt["experiment_id"]
        results.append(receipt)
    centers, rabi, ramsey = results
    assert abs(rabi["metrics"]["best_piAmp"] - service.original_qubits.get(1).piAmp * 1.02) < .01
    assert abs(ramsey["metrics"]["correction"] - 100e3) < 30e3
    snapshot = json.loads((Path(ramsey["input_directory"]) / "qubits.json").read_text(encoding="utf-8"))["qubits"]["1"]
    assert snapshot["readoutCenters"]["0"] == centers["metrics"]["center0"]
    assert snapshot["piAmp"] == rabi["metrics"]["best_piAmp"]
    assert source_digest() == before


def test_default_backend_uses_snapshot_and_driver_without_eager_connection(tmp_path, monkeypatch):
    import qagent.integrations.quantumx.service as module
    seen = []

    class Backend:
        def __init__(self, hardware_cfg, qubits_cfg, driver_dir=None):
            seen.append((hardware_cfg, qubits_cfg, driver_dir))
            self.qubit, self.index = qubits_cfg.get(1), 0

        @contextmanager
        def session(self):
            yield self

        def run(self, pulse_ir, shots):
            center = self.qubit.readoutCenters[self.index]
            self.index += 1
            return np.tile(center, (shots, 1))

    monkeypatch.setattr(module, "load_backend_class", lambda source: Backend)
    service = make_service(tmp_path, backend_factory=None, settings={"driver_dir": tmp_path / "drivers"})
    assert seen == []
    receipt = service.run_sync("readout_centers")
    assert receipt["status"] == "completed" and receipt["data_origin"] == "hardware"
    assert len(seen) == 1 and seen[0][2] == tmp_path / "drivers"
    assert seen[0][1].get(1).f10 == service.original_qubits.get(1).f10


def test_input_defaults_are_loaded_and_explicit_parameters_override(tmp_path):
    path = tmp_path / "inputs.json"
    path.write_text('{"n":2,"shots":100,"validation_fraction":0.25}', encoding="utf-8")
    service = make_service(tmp_path, settings={"parameter_files": {"readout_centers": path}})
    first = service.run_sync("readout_centers")
    second = service.run_sync("readout_centers", {"shots": 200})
    assert first["n"] == 2 and first["actual_measurements"] == 200
    assert second["n"] == 2 and second["actual_measurements"] == 400


def test_budget_idempotency_query_and_recovery(tmp_path):
    service = make_service(tmp_path, settings={"max_experiments": 1, "max_measurements": 1024})
    first = service.start("readout_centers", {}, "budget", "call")
    service.wait()
    assert service.start("readout_centers", {}, "budget", "call")["experiment_id"] == first["experiment_id"]
    assert service.recover_operation("budget", "call")["recovered_receipt"]
    assert service.recover_operation("budget", "missing")["interrupted"]
    with pytest.raises(ValueError, match="同一"):
        service.start("readout_centers", {"shots": 100}, "budget", "call")
    with pytest.raises(ValueError, match="预算"):
        service.start("readout_centers", {}, "budget", "new")
    assert len(service.list_results()["experiments"]) == 1
    assert service.compare(first["experiment_id"], first["experiment_id"])["first"]["status"] == "completed"
    restarted = make_service(tmp_path, settings={"max_experiments": 1, "max_measurements": 1024})
    with pytest.raises(ValueError, match="预算"):
        restarted.start("readout_centers", {}, "budget", "new-process")


def test_failure_preserves_partial_iq_and_human_report(tmp_path):
    class FailingBackend(FixtureIQBackend):
        def run(self, pulse_ir, shots):
            if self.index:
                raise RuntimeError("PRIVATE_VENDOR_BODY")
            return super().run(pulse_ir, shots)

    service = make_service(tmp_path, backend_factory=FailingBackend)
    result = service.run_sync("readout_centers")
    assert result["status"] == "failed" and result["actual_measurements"] == 512
    assert result["reserved_measurements"] == 1024
    output = Path(result["artifact_directory"])
    assert (output / "iq-0000.npy").exists() and (output / "iq-0000.csv").exists()
    assert (output / "iq.png").exists() and (output / "summary.html").exists()
    assert not (output / "result.json").exists()
    assert "PRIVATE_VENDOR_BODY" not in (output / "summary.html").read_text(encoding="utf-8")


def test_failure_before_acquisition_has_status_page_without_fake_data_links(tmp_path):
    def broken(context):
        raise ImportError("PRIVATE_DRIVER_DETAILS")

    service = make_service(tmp_path, backend_factory=broken)
    result = service.run_sync("readout_centers")
    assert result["status"] == "failed" and result["actual_measurements"] == 0
    page = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert 'href="acquisitions.json"' not in page and 'src="iq.png"' not in page
    assert "PRIVATE_DRIVER_DETAILS" not in page


def test_persistent_stop_and_no_replay(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingBackend(FixtureIQBackend):
        def run(self, pulse_ir, shots):
            if self.index == 0:
                entered.set()
                assert release.wait(10)
            return super().run(pulse_ir, shots)

    service = make_service(tmp_path, backend_factory=BlockingBackend)
    receipt = service.start("readout_centers", {}, "stop", "call")
    try:
        assert entered.wait(10)
        reader = make_service(tmp_path, backend_factory=BlockingBackend)
        assert reader.stop(receipt["experiment_id"])["stop_requested"]
        with pytest.raises(ValueError, match="未结束"):
            reader.start("readout_centers", {}, "another", "call2")
    finally:
        release.set()
        service.wait()
    result = service.public_result(receipt["experiment_id"])
    assert result["status"] == "interrupted" and result["actual_measurements"] == 512
    assert Path(result["summary_path"]).exists()
    assert len(service.list_results()["experiments"]) == 1


def test_abandoned_status_is_not_replayed(tmp_path):
    service = make_service(tmp_path)
    result = service.run_sync("readout_centers")
    report = service.get(result["experiment_id"])
    report.update(status="running", iq_records=[], actual_measurements=0)
    service._save(report)
    reader = make_service(tmp_path)
    status = reader.public_result(result["experiment_id"])
    assert "未自动重放" in status["execution_note"] and status["actual_measurements"] == 1024
    assert reader.stop(result["experiment_id"])["status"] == "interrupted"


def test_legacy_simulation_journal_does_not_block_hardware_generation(tmp_path):
    service = make_service(tmp_path)
    with service.store.connect() as db:
        db.execute("CREATE TABLE quantumx_experiments (id TEXT, session_id TEXT, cost INTEGER, report TEXT, identity TEXT)")
        db.execute("INSERT INTO quantumx_experiments VALUES(?,?,?,?,?)",
                   ("old", "legacy", 100, '{"status":"running","data_origin":"simulated"}', "legacy"))
    result = service.run_sync("readout_centers")
    assert result["status"] == "completed"
    with service.store.connect() as db:
        assert db.execute("SELECT report FROM quantumx_experiments WHERE id='old'").fetchone()[0] == '{"status":"running","data_origin":"simulated"}'


def test_export_error_never_changes_saved_acquisition_or_stop(tmp_path, monkeypatch):
    import qagent.integrations.quantumx.presentation as presentation

    def locked(*args):
        raise PermissionError("file is open in a viewer")

    monkeypatch.setattr(presentation, "render_experiment", locked)
    service = make_service(tmp_path)
    result = service.run_sync("readout_centers")
    assert result["status"] == "completed" and result["actual_measurements"] == 1024
    assert result["export_error_type"] == "PermissionError"
    report = service.get(result["experiment_id"])
    report["status"] = "running"
    service._save(report)
    stopped = service.stop(result["experiment_id"])
    assert stopped["status"] == "interrupted" and stopped["actual_measurements"] == 1024


@pytest.mark.parametrize("parameters", [{"points": 6}, {"amp_start": .1}, {"shots": 0}, {"backend": "arbitrary"}, {"update": True}, {"shots": True}])
def test_parameter_surface_is_narrow(parameters):
    with pytest.raises(ValidationError):
        RabiParameters.model_validate(parameters)


@pytest.mark.parametrize("parameters", [{"delay_stop_s": 0}, {"fringe_freq_hz": 100e6}, {"fringe_freq_hz": float("inf")}])
def test_ramsey_grid_restrictions(parameters):
    with pytest.raises(ValidationError):
        RamseyParameters.model_validate(parameters)


class Pulse:
    def to_dict(self):
        return {"events": []}


@pytest.mark.parametrize("raw", [np.zeros((10,)), np.zeros((10, 3)), np.ones((10, 2), dtype=complex), np.full((10, 2), np.nan)])
def test_bad_iq_is_rejected_before_recording(tmp_path, raw):
    service = make_service(tmp_path)
    class Backend:
        def run(self, *args):
            return raw
    recorder = RecordingBackend(Backend(), service.store, "bad", [{}])
    with pytest.raises(ValueError):
        recorder.run(Pulse(), 10)
    assert not recorder.records


def test_unreserved_shots_rejected_before_backend(tmp_path):
    service = make_service(tmp_path)
    class Backend:
        def run(self, *args):
            raise AssertionError("must not acquire")
    recorder = RecordingBackend(Backend(), service.store, "bad", [{}], expected_shots=10)
    with pytest.raises(ValueError, match="reserved"):
        recorder.run(Pulse(), 11)


def test_nonfinite_values_are_explicit_nulls():
    warnings = []
    assert json_ready({"T2Star": np.float64(np.inf)}, warnings) == {"T2Star": None}
    assert warnings
