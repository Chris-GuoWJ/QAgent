"""IQ contracts, numerical processing, storage, and explicit V1 compatibility."""

import hashlib
import json
import sqlite3
import time

import numpy as np
import pytest
from pydantic import ValidationError

from conftest import finish
from qagent.analysis import analyze, derive_spectrum
from qagent.backend.simulator import SimulationSettings, SimulatorBackend, create_backend
from qagent.schemas import IQData, Measurement, ScanConfig


def payload():
    return {"request_id": "iq-request", "job_id": "iq-job", "qubit": "Q0",
            "experiment_type": "spectroscopy",
            "config": {"start_hz": 4.8e9, "stop_hz": 5.2e9, "points": 9, "shots_per_point": 4},
            "frequencies": np.linspace(4.8e9, 5.2e9, 9).tolist(),
            "iq": {"i": [2.0] * 9, "q": [-3.0] * 9}}


def test_default_measurement_is_iq_and_round_trips_signed_unbounded_channels():
    m = Measurement.model_validate(payload())
    assert m.data_type == "iq" and m.schema_version == 2 and m.y_unit == "a.u."
    assert m.iq.mode == "averaged" and m.values is None
    assert m.iq.i[0] == 2 and m.iq.q[0] == -3
    assert Measurement.model_validate_json(m.model_dump_json()) == m


@pytest.mark.parametrize("changes", [
    {"iq": None},
    {"iq": {"i": [1.0] * 8, "q": [1.0] * 9}},
    {"iq": {"i": [[1.0] * 4] * 9, "q": [[1.0] * 4] * 9}},
    {"iq": {"mode": "single_shot", "i": [[1.0] * 3] * 9, "q": [[1.0] * 4] * 9}},
    {"iq": {"mode": "single_shot", "i": [[1.0] * 4] * 9, "q": [1.0] * 9}},
    {"iq": {"i": [float("nan")] * 9, "q": [1.0] * 9}},
    {"iq": {"mode": "single_shot", "i": [[float("inf")] * 4] * 9, "q": [[1.0] * 4] * 9}},
    {"values": [0.5] * 9},
    {"y_unit": "probability"},
    {"schema_version": 1},
])
def test_iq_rejects_wrong_shapes_nonfinite_and_mixed_probability(changes):
    with pytest.raises(ValidationError):
        Measurement.model_validate(payload() | changes)


def test_values_without_an_explicit_probability_label_are_not_reinterpreted():
    data = payload()
    data.pop("iq")
    data["values"] = [0.5] * 9
    with pytest.raises(ValidationError):
        Measurement.model_validate(data)


def test_old_serialized_probability_stays_probability():
    data = payload()
    data.pop("iq")
    data.update(y_unit="probability", values=[0.5] * 9)
    m = Measurement.model_validate_json(json.dumps(data))
    assert m.schema_version == 1 and m.data_type == "probability" and m.iq is None
    assert m.response_mode == "peak" and derive_spectrum(m)["values"] == [0.5] * 9
    with pytest.raises(ValidationError):
        Measurement.model_validate(data | {"values": [1.2] * 9})


def test_single_shots_are_averaged_as_complex_samples_before_magnitude():
    data = payload()
    data["iq"] = {"mode": "single_shot", "i": [[1, -1, 1, -1]] * 9,
                  "q": [[2, -2, 2, -2]] * 9}
    m = Measurement.model_validate(data)
    spectrum = derive_spectrum(m)
    assert spectrum["amplitude"] == [0.0] * 9
    assert spectrum["mean_i"] == spectrum["mean_q"] == [0.0] * 9
    assert Measurement.model_validate_json(m.model_dump_json()) == m


def test_phase_is_preserved_and_unwrapped_without_probability_conversion():
    phase = np.linspace(2.8, 3.4, 9)
    data = payload() | {"y_unit": "V", "iq": {
        "i": (2 * np.cos(phase)).tolist(), "q": (2 * np.sin(phase)).tolist()}}
    spectrum = derive_spectrum(Measurement.model_validate(data))
    assert spectrum["y_unit"] == "V"
    np.testing.assert_allclose(spectrum["amplitude"], 2)
    np.testing.assert_allclose(spectrum["phase_radians"], phase)


@pytest.mark.parametrize("mode", ["peak", "dip"])
@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_iq_fit_handles_both_signs_and_physical_scales(task, mode, scale):
    x = np.linspace(4.98e9, 5.08e9, 201)
    sign = -1 if mode == "dip" else 1
    shape = 0.65 * (5e6)**2 / ((x - 5.0317e9)**2 + (5e6)**2)
    y = (2.0 + sign * shape) * scale
    rng = np.random.default_rng(42)
    i = y * np.cos(1.7) + rng.normal(0, 0.002 * scale, len(x))
    q = y * np.sin(1.7) + rng.normal(0, 0.002 * scale, len(x))
    m = Measurement(request_id="fit", job_id="fit", qubit="Q0", experiment_type="spectroscopy",
        config=ScanConfig(start_hz=x[0], stop_hz=x[-1], points=len(x), shots_per_point=256),
        frequencies=x.tolist(), response_mode=mode, iq=IQData(i=i.tolist(), q=q.tolist()))
    result = analyze(m, task, "fit.json")
    assert result.trusted and result.method == "iq_amplitude_lorentzian_v2"
    assert abs(result.frequency_hz - 5.0317e9) < 100e3
    assert result.uncertainty_hz > 0 and result.data_type == "iq" and result.response_mode == mode


def test_flat_iq_is_not_a_trusted_resonance(task):
    result = analyze(Measurement.model_validate(payload()), task, "flat.json")
    assert not result.trusted and result.issue == "flat_response"


@pytest.mark.parametrize("iq_mode", ["averaged", "single_shot"])
@pytest.mark.parametrize("mode", ["peak", "dip"])
def test_simulator_outputs_actual_iq_with_declared_storage_mode(tmp_path, iq_mode, mode):
    from qagent.schemas import ExperimentRequest

    settings = SimulationSettings(iq_mode=iq_mode, response_mode=mode, baseline=5, contrast=2,
                                  phase_radians=-0.7, noise_std=0, duration_seconds=0)
    backend = SimulatorBackend(tmp_path / "iq.sqlite", settings)
    request = ExperimentRequest(request_id="iq-simulation", qubit="Q0", experiment_type="spectroscopy",
                                purpose="search", config=ScanConfig.model_validate(payload()["config"]))
    m = backend.fetch(backend.submit(request))
    assert m.iq.mode == iq_mode and m.data_type == "iq" and m.values is None
    spectrum = derive_spectrum(m)
    assert min(spectrum["amplitude"]) > 1 and max(spectrum["mean_q"]) < 0
    if iq_mode == "single_shot":
        assert len(m.iq.i[0]) == request.config.shots_per_point
    else:
        assert isinstance(m.iq.i[0], float)


@pytest.mark.parametrize("iq_mode", ["averaged", "single_shot"])
def test_iq_calibration_stores_channels_spectra_and_independent_verification(make_controller, task, iq_mode):
    controller = make_controller(SimulationSettings(duration_seconds=0, iq_mode=iq_mode))
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success" and state.verifications[-1].passed
    assert all(a.data_type == "iq" and a.response_mode == "dip" for a in state.analyses)
    for a in state.analyses:
        raw = controller.store.read_artifact(a.data_ref)
        assert raw["data_type"] == "iq" and raw["iq"]["mode"] == iq_mode
        curve = controller.store.read_artifact(a.spectrum_ref)
        assert "amplitude" in curve and "phase_radians" in curve and curve["data_type"] == "iq"
    report = controller.report(state.run_id)
    assert report["measurement_type"] == "iq" and report["response_mode"] == "dip"
    assert state.used_measurements == sum(r.config.cost for r in state.requests if r.charged)


def test_explicit_probability_simulation_still_completes_calibration(make_controller, task):
    controller = make_controller(SimulationSettings(data_type="probability", duration_seconds=0))
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success" and state.verifications[-1].passed
    assert all(a.data_type == "probability" and a.method == "lorentzian_least_squares_v1" for a in state.analyses)


def test_new_iq_default_keeps_v1_database_and_fingerprint_untouched(tmp_path):
    from qagent.schemas import ExperimentRequest

    original_settings = {"true_frequency_hz": 5.0317e9, "linewidth_hz": 5e6, "noise_std": 0.2,
        "contrast": 0.65, "duration_seconds": 0.05, "seed": 42, "fail_jobs": False,
        "ambiguous_submit": False, "unknown_lookup": False}
    serialized = json.dumps(original_settings, separators=(",", ":"))
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    path = tmp_path / "backend.sqlite"
    old_data = payload()
    old_data.pop("iq")
    old_data.update(y_unit="probability", values=[0.5] * 9)
    request = ExperimentRequest(request_id=old_data["request_id"], qubit="Q0", experiment_type="spectroscopy",
                                purpose="search", config=ScanConfig.model_validate(old_data["config"]))
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE jobs (request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, request TEXT NOT NULL, created REAL NOT NULL, result TEXT NOT NULL)")
        db.execute("INSERT INTO metadata VALUES('settings', ?)", (fingerprint,))
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (request.request_id, old_data["job_id"],
            SimulatorBackend._request_json(request), time.time() - 100, json.dumps(old_data)))
    old = create_backend({"data_type": "probability"}, tmp_path)
    assert old._settings.scenario_json() == serialized
    expected_identity = "backend:" + hashlib.sha256((str(path.resolve()) + fingerprint).encode()).hexdigest()
    assert old.identity == expected_identity and old.fetch(old_data["job_id"]).schema_version == 1
    new = create_backend({}, tmp_path)
    assert new.path.name == "backend-iq.sqlite" and new.identity != old.identity
    assert old.fetch(old_data["job_id"]).data_type == "probability"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0] == fingerprint
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.parametrize("wrong_type", [False, True])
def test_controller_rejects_backend_changing_type_or_response_mode(make_controller, task, wrong_type):
    controller = make_controller()
    original = controller.backend.fetch

    def altered(job_id):
        m = original(job_id)
        if wrong_type:
            fields = m.model_dump(exclude={"iq", "values", "y_unit", "data_type", "response_mode"})
            return Measurement(**fields, data_type="probability", values=[0.5] * m.config.points)
        return m.model_copy(update={"response_mode": "peak"})

    controller.backend.fetch = altered
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed" and not state.verifications and not state.analyses
    assert state.experiments == 1 and state.used_measurements > 0
