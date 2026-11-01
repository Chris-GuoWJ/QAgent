"""Optional integration tests run the selected local QuantumX functions, never hardware."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from contextlib import contextmanager
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
import numpy as np
import pytest
from pydantic import ValidationError

from qagent.bootstrap import RuntimeSettings, create_controller
from qagent.chat import ChatAgent
from qagent.chat_history import ChatHistory
from qagent.chat_tools import ChatOperations
from qagent.integrations.quantumx.parameters import QuantumXSettings, RabiParameters, RamseyParameters
from qagent.integrations.quantumx.recording_backend import RecordingBackend
from qagent.integrations.quantumx.results import json_ready
from qagent.integrations.quantumx.service import QuantumXService
from qagent.integrations.quantumx.simulation import SimulatedFunctionBackend
from qagent.storage import Store


SOURCE = Path(__file__).resolve().parents[2] / "QuantumX2"
pytestmark = pytest.mark.skipif(not (SOURCE / "src/quantumx/calibration.py").exists(), reason="Optional sibling QuantumX2 source not installed")


@pytest.fixture
def service(tmp_path):
    return QuantumXService(Store(tmp_path / "runs"), QuantumXSettings(source_dir=SOURCE))


def tree_digest():
    paths = sorted(p for root in (SOURCE / "src", SOURCE / "configs") for p in root.rglob("*")
                   if p.is_file() and p.suffix in {".py", ".json", ".pyc"})
    return {str(p.relative_to(SOURCE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_original_functions_full_chain_and_iq_round_trip(tmp_path):
    before = tree_digest()
    service = QuantumXService(Store(tmp_path / "runs"), QuantumXSettings(source_dir=SOURCE))
    center = service.run_sync("readout_centers", session_id="chain")
    rabi = service.run_sync("pi_amplitude", {"reference_experiment_id": center["experiment_id"]}, "chain")
    ramsey = service.run_sync("ramsey_frequency", {"reference_experiment_id": rabi["experiment_id"]}, "chain")
    for receipt, acquisitions in ((center, 2), (rabi, 41), (ramsey, 62)):
        assert receipt["status"] == "completed", receipt
        assert receipt["quality"] == {"accepted": True, "status": "candidate", "independently_verified": False}
        assert receipt["data_origin"] == "simulated"
        assert receipt["actual_measurements"] == receipt["reserved_measurements"] == acquisitions * 512
        report = service.get(receipt["experiment_id"])
        assert len(report["iq_records"]) == acquisitions
        for record in report["iq_records"]:
            array = np.load(service.store.root / record["data_ref"], allow_pickle=False)
            assert array.shape == (512, 2) and np.isfinite(array).all()
            ir = service.store.read_artifact(record["pulse_ir_ref"])
            assert isinstance(ir, dict) and ir
        result = service.store.read_artifact(report["result_ref"])
        assert result["updated"] is False
        assert "iq_records" not in receipt and "iq0" not in receipt["metrics"]
    assert center["metrics"]["fidelity"]["avg"] > .95
    expected_pi = service.original_qubits.get(1).piAmp * 1.02
    assert abs(rabi["metrics"]["best_piAmp"] - expected_pi) < .01
    expected_f10 = service.original_qubits.get(1).f10 + 100e3
    assert abs(ramsey["metrics"]["best_f10"] - expected_f10) < 30e3
    rabi_report = service.get(rabi["experiment_id"])
    ramsey_report = service.get(ramsey["experiment_id"])
    copied = service.store.read_artifact(ramsey_report["config_snapshot"]["qubits"])["qubits"]["1"]
    assert copied["readoutCenters"]["0"] == center["metrics"]["center0"]
    assert copied["piAmp"] == rabi["metrics"]["best_piAmp"]
    assert [r["labels"]["amplitude"] for r in rabi_report["iq_records"]] == rabi_report["axes"][0]["values"]
    assert [r["labels"]["series"] for r in ramsey_report["iq_records"]] == ["X", "Y"] * 31
    assert tree_digest() == before  # Includes bytecode: no writes into the source repository.


class Pulse:
    def to_dict(self):
        return {"pulses": []}


def test_recorder_is_transparent_and_preserves_session(tmp_path):
    raw = np.arange(20, dtype=float).reshape(10, 2)
    seen = []

    class Backend:
        @contextmanager
        def session(self):
            seen.append("open")
            try:
                yield self
            finally:
                seen.append("close")

        def run(self, pulse_ir, shots):
            seen.append(shots)
            return raw

    store = Store(tmp_path)
    recorder = RecordingBackend(Backend(), store, "experiment", [{"state": 0}])
    with recorder.session():
        assert recorder.run(Pulse(), 10) is raw
        with pytest.raises(ValueError, match="more acquisition"):
            recorder.run(Pulse(), 10)
    assert seen == ["open", 10, "close"]
    np.testing.assert_array_equal(np.load(store.root / recorder.records[0]["data_ref"]), raw)


@pytest.mark.parametrize("array", [np.zeros((10,)), np.zeros((10, 3)), np.full((10, 2), np.nan), np.ones((10, 2), dtype=complex)])
def test_recorder_rejects_invalid_iq(tmp_path, array):
    recorder = RecordingBackend(SimpleNamespace(run=lambda *args: array), Store(tmp_path), "bad", [{}])
    with pytest.raises(ValueError):
        recorder.run(Pulse(), 10)
    assert not recorder.records


@pytest.mark.parametrize("parameters", [{"points": 6}, {"amp_start": .1}, {"points": True}, {"shots": 0}, {"shots": float("nan")}, {"update": True}, {"backend": "arbitrary"}])
def test_model_cannot_expand_function_surface(parameters):
    with pytest.raises(ValidationError):
        RabiParameters.model_validate(parameters)


@pytest.mark.parametrize("parameters", [{"delay_stop_s": 0}, {"delay_start_s": 1e-6, "delay_stop_s": 1e-6}, {"fringe_freq_hz": 100e6}, {"fringe_freq_hz": float("inf")}])
def test_ramsey_grid_validation(parameters):
    with pytest.raises(ValidationError):
        RamseyParameters.model_validate(parameters)


def test_target_registry_budget_and_idempotency(service):
    with pytest.raises(ValueError, match="未知"):
        service.start("__import__", {}, "session", "bad")
    with pytest.raises(ValidationError):
        service.start("readout_centers", {"n": 0}, "session", "bad")
    with pytest.raises(ValueError, match="目标"):
        service.start("readout_centers", {"n": 3}, "session", "bad")
    receipt = service.start("readout_centers", {}, "session", "same-call")
    service.wait()
    duplicate = service.start("readout_centers", {}, "session", "same-call")
    assert duplicate["experiment_id"] == receipt["experiment_id"]
    assert len(service.list_results()["experiments"]) == 1
    with pytest.raises(ValueError, match="同一工具"):
        service.start("readout_centers", {"shots": 1000}, "session", "same-call")
    assert service.recover_operation("session", "same-call")["recovered_receipt"]
    assert service.recover_operation("session", "missing")["interrupted"]
    assert len(service.list_results()["experiments"]) == 1


def test_budget_is_reserved_and_persists_after_restart(tmp_path):
    settings = QuantumXSettings(source_dir=SOURCE, max_experiments=1, max_measurements=1024)
    service = QuantumXService(Store(tmp_path), settings)
    first = service.run_sync("readout_centers", session_id="budget")
    assert first["actual_measurements"] == 1024
    reloaded = QuantumXService(Store(tmp_path), settings)
    with pytest.raises(ValueError, match="预算"):
        reloaded.start("readout_centers", {}, "budget", "extra")
    with pytest.raises(ValueError, match="预算"):
        reloaded.start("pi_amplitude", {}, "new-session", "too-expensive")


def test_failed_acquisition_keeps_partial_iq_and_reservation(tmp_path):
    class FailingBackend(SimulatedFunctionBackend):
        def run(self, pulse_ir, shots):
            if self.index == 1:
                raise RuntimeError("sensitive vendor details must not reach model")
            return super().run(pulse_ir, shots)

    service = QuantumXService(Store(tmp_path), QuantumXSettings(source_dir=SOURCE), FailingBackend)
    receipt = service.run_sync("readout_centers", session_id="fail")
    assert receipt["status"] == "failed" and receipt["error_type"] == "RuntimeError"
    assert receipt["reserved_measurements"] == 1024 and receipt["actual_measurements"] == 512
    assert receipt["iq_acquisitions"] == 1 and "sensitive" not in json.dumps(receipt)
    report = service.get(receipt["experiment_id"])
    assert (service.store.root / report["iq_records"][0]["data_ref"]).exists()


def test_stop_is_persistent_and_does_not_replay(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingBackend(SimulatedFunctionBackend):
        def run(self, pulse_ir, shots):
            if self.index == 0:
                entered.set()
                assert release.wait(10)
            return super().run(pulse_ir, shots)

    service = QuantumXService(Store(tmp_path), QuantumXSettings(source_dir=SOURCE), BlockingBackend)
    receipt = service.start("readout_centers", {}, "stop", "start")
    try:
        assert entered.wait(10)
        reader = QuantumXService(Store(tmp_path), service.settings, BlockingBackend)
        stopped = reader.stop(receipt["experiment_id"])
        assert stopped["stop_requested"]
        with pytest.raises(ValueError, match="未结束"):
            reader.start("readout_centers", {}, "another", "new")
    finally:
        release.set()
        service.wait()
    result = service.public_result(receipt["experiment_id"])
    assert result["status"] == "interrupted" and result["actual_measurements"] == 512
    assert service.recover_operation("stop", "start")["experiment_id"] == receipt["experiment_id"]


def test_pause_stops_at_next_call_boundary(tmp_path):
    class PausingBackend(SimulatedFunctionBackend):
        def run(self, pulse_ir, shots):
            raw = super().run(pulse_ir, shots)
            service.pause()
            return raw

    service = QuantumXService(Store(tmp_path), QuantumXSettings(source_dir=SOURCE), PausingBackend)
    result = service.run_sync("readout_centers")
    assert result["status"] == "interrupted" and result["actual_measurements"] == 512


def test_query_compare_and_reference_validation(service):
    a = service.run_sync("readout_centers", session_id="queries")
    b = service.run_sync("readout_centers", {"n": 2}, "queries")
    with pytest.raises(ValueError, match="比较"):
        service.compare(a["experiment_id"], b["experiment_id"])
    with pytest.raises(ValueError, match="引用"):
        service.start("pi_amplitude", {"n": 2, "reference_experiment_id": a["experiment_id"]}, "queries", "bad-reference")
    comparison = service.compare(a["experiment_id"], a["experiment_id"])
    assert comparison["first"]["experiment_id"] == a["experiment_id"]
    assert len(service.list_results()["experiments"]) == 2


def test_nonfinite_fit_value_is_valid_json():
    warnings = []
    value = json_ready({"T2Star": np.float64(float("inf")), "x": np.array([1., 2.])}, warnings)
    assert value == {"T2Star": None, "x": [1., 2.]}
    assert warnings and "null" in json.dumps(value, allow_nan=False)


def test_recorder_rejects_unreserved_shots_before_acquisition(tmp_path):
    seen = []
    backend = SimpleNamespace(run=lambda *args: seen.append(args))
    recorder = RecordingBackend(backend, Store(tmp_path), "budget", [{}], expected_shots=10)
    with pytest.raises(ValueError, match="reserved"):
        recorder.run(Pulse(), 11)
    assert seen == []


def test_abandoned_execution_can_be_stopped_without_replaying(service):
    result = service.run_sync("readout_centers", session_id="abandoned")
    report = service.get(result["experiment_id"])
    report["status"] = "running"  # Simulate persisted state left by a terminated process.
    report["iq_records"] = []
    report["actual_measurements"] = 0
    service._save(report)
    reloaded = QuantumXService(service.store, service.settings)
    uncertain = reloaded.public_result(report["experiment_id"])
    assert uncertain["executor_active_here"] is False and uncertain["actual_measurements"] == 1024
    assert "未自动重放" in uncertain["execution_note"]
    stopped = reloaded.stop(report["experiment_id"])
    assert stopped["status"] == "interrupted" and stopped["actual_measurements"] == 1024
    assert len(reloaded.list_results()["experiments"]) == 1


def test_chat_allows_only_one_new_experiment_per_turn(make_controller, task):
    controller = make_controller()
    controller.quantumx_service = QuantumXService(controller.store, QuantumXSettings(source_dir=SOURCE))
    history = ChatHistory(controller.store, "one-experiment")
    operations = ChatOperations(controller, task, history)
    from qagent.integrations.quantumx.tools import create_tools

    tools = {t.name: t for t in create_tools(operations, lambda: "call")}
    tools["measure_readout_centers"].invoke({"n": 1})
    operations.wait()
    with pytest.raises(ValueError, match="本条指令"):
        tools["tune_pi_amplitude"].invoke({"n": 1})
    with pytest.raises(ValueError, match="本条指令"):
        operations.start("legacy", qubit="Q0")
    assert not controller.store.list_runs()
    operations.close()


def test_chat_tool_dispatch_and_lost_receipt_recovery(make_controller, task):
    controller = make_controller()
    controller.quantumx_service = QuantumXService(controller.store, QuantumXSettings(source_dir=SOURCE))
    history = ChatHistory(controller.store, "quantumx-test")
    operations = ChatOperations(controller, task, history)

    class Model:
        def __init__(self):
            self.responses = [AIMessage(content="", tool_calls=[{"name": "measure_readout_centers", "args": {"n": 1}, "id": "qx-1"}]),
                              AIMessage(content="已调度 q1 模拟读出中心实验。")]

        def bind_tools(self, tools):
            assert {"start_calibration", "measure_readout_centers", "tune_frequency_ramsey"} <= {t.name for t in tools}
            return self

        def invoke(self, messages):
            assert "模拟 IQ" in messages[0].content
            return self.responses.pop(0)

    agent = ChatAgent(Model(), operations)
    assert "模拟" in agent.respond("用 QuantumX 计算 q1 读出中心")
    operations.wait()
    messages = history.messages()
    receipt = json.loads(next(m.content for m in messages if isinstance(m, ToolMessage)))
    assert receipt["experiment_type"] == "readout_centers"
    # Missing tool reply must recover an existing receipt, or report interruption, never start again.
    for identifier in ("qx-1", "missing"):
        history.append(AIMessage(content="", tool_calls=[{"name": "measure_readout_centers", "args": {"n": 1}, "id": identifier}]))
        agent._repair_pending_calls()
    assert len(operations.quantumx.list_results()["experiments"]) == 1
    operations.close()
    assert operations.closing


def test_opt_in_bootstrap_and_session_identity(tmp_path):
    disabled = create_controller(Store(tmp_path / "default"), RuntimeSettings())
    assert not hasattr(disabled, "quantumx_service")
    settings = {"source_dir": str(SOURCE)}
    enabled = create_controller(Store(tmp_path / "enabled"), RuntimeSettings(quantumx=settings))
    enabled.quantumx_service.bind_session("same")
    changed = QuantumXService(enabled.store, QuantumXSettings(source_dir=SOURCE, simulation_seed=43))
    with pytest.raises(ValueError, match="变化"):
        changed.bind_session("same")


def test_cli_demo_runs_without_model_credentials(tmp_path):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"quantumx": {"source_dir": str(SOURCE)}}), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "qagent", "--storage", str(tmp_path / "runs"), "quantumx", "demo", "--runtime", str(runtime)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert len(report["experiments"]) == 3
    assert all(r["status"] == "completed" and r["data_origin"] == "simulated" for r in report["experiments"])
