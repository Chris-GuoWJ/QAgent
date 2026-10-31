import sqlite3
import time

import pytest

from conftest import ReplayBackend, finish
from qagent.backend.base import SubmissionUnknown
from qagent.backend.simulator import SimulationSettings, SimulatorBackend
from qagent.controller import Controller
from qagent.policy import FixedPolicy
from qagent.schemas import Decision, ScanConfig
from qagent.state import build_agent_view
from qagent.storage import RunBusy


def job_count(backend):
    with sqlite3.connect(backend.path) as db:
        return db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_complete_calibration_requires_independent_verification(make_controller, task):
    controller = make_controller()
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"
    assert state.experiments >= 3
    assert state.verifications[-1].passed
    candidate = next(a for a in state.analyses if a.analysis_id == state.candidate_id)
    validation = state.analyses[-1]
    assert candidate.data_ref != validation.data_ref
    assert abs(candidate.frequency_hz - 5.0317e9) < task.verification_tolerance_hz
    assert state.used_measurements == sum(r.config.cost for r in state.requests if r.charged)
    assert state.reserved_measurements == 0
    report = controller.report(state.run_id)
    assert report["metadata"] == {}
    assert controller.store.read_artifact(state.run_id + "/report.json")["outcome"] == "success"


@pytest.mark.parametrize("seed", [0, 3, 17])
def test_multiple_noise_conditions(make_controller, task, seed):
    controller = make_controller(SimulationSettings(duration_seconds=0, seed=seed))
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"


def test_budget_exhaustion_never_submits(make_controller, task):
    controller = make_controller()
    state = finish(controller, controller.create(task.model_copy(update={"max_measurements": 1000})).run_id)
    assert state.outcome == "not_converged"
    assert job_count(controller.backend) == 0
    assert state.used_measurements == state.reserved_measurements == 0


def test_experiment_limit_includes_verification(make_controller, task):
    controller = make_controller()
    state = finish(controller, controller.create(task.model_copy(update={"max_experiments": 1})).run_id)
    assert state.outcome == "not_converged"
    assert state.experiments == 1
    assert not state.verifications


class BadPolicy:
    identity = "bad-policy"

    def choose(self, view):
        return Decision(action="scan", scan=ScanConfig(start_hz=4.1e9, stop_hz=5e9, points=81,
                        shots_per_point=100), reason="Out of authorized range")


def test_out_of_range_rejected_before_backend(make_controller, task):
    controller = make_controller(policy=BadPolicy())
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed"
    assert state.errors == task.max_errors
    assert job_count(controller.backend) == 0
    assert state.experiments == 0


def test_unknown_evidence_is_rejected(make_controller, task):
    class Policy(BadPolicy):
        def choose(self, view):
            return Decision(action="finish", evidence_ids=["invented"], reason="claim")
    controller = make_controller(policy=Policy())
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed"
    assert "unknown evidence" in state.reason


def test_finish_cannot_claim_calibration_success(make_controller, task):
    class Policy(BadPolicy):
        def choose(self, view):
            return Decision(action="finish", reason="Calibration succeeded")
    controller = make_controller(policy=Policy())
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "not_converged"


def test_stop_before_submission_releases_reservation(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    controller.step(state.run_id)
    assert controller.store.load(state.run_id).reserved_measurements > 0
    controller.store.request_stop(state.run_id)
    state = controller.step(state.run_id)
    assert state.outcome == "stopped"
    assert state.reserved_measurements == 0
    assert job_count(controller.backend) == 0


def test_stop_preserves_inflight_receipt(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    controller.step(state.run_id)
    controller.step(state.run_id)
    controller.store.request_stop(state.run_id)
    state = controller.step(state.run_id)
    assert state.outcome == "stopped"
    assert state.current.job_id
    assert state.used_measurements > 0
    assert job_count(controller.backend) == 1


def test_unknown_submission_uses_lookup_without_duplicate(make_controller, task):
    controller = make_controller(SimulationSettings(duration_seconds=0, ambiguous_submit=True))
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"
    assert job_count(controller.backend) == state.experiments
    assert state.reserved_measurements == 0


def test_unresolvable_submission_remains_pending(make_controller, task):
    controller = make_controller(SimulationSettings(duration_seconds=0, ambiguous_submit=True, unknown_lookup=True))
    state = finish(controller, controller.create(task).run_id)
    assert state.stage == "reconcile" and state.outcome is None
    before = job_count(controller.backend)
    for _ in range(3):
        controller.step(state.run_id)
    state = controller.store.load(state.run_id)
    assert job_count(controller.backend) == before == 1
    assert state.reserved_measurements > 0 and state.used_measurements == 0
    controller.store.request_stop(state.run_id)
    assert controller.step(state.run_id).stage == "reconcile"


def test_crash_after_backend_acceptance_recovers_existing_job(make_controller, task):
    controller = make_controller()
    original = controller.backend
    state = controller.create(task)
    controller.step(state.run_id)

    class ProcessCrash(BaseException):
        pass

    class CrashingBackend:
        def __getattr__(self, name):
            return getattr(original, name)

        def submit(self, request):
            original.submit(request)
            raise ProcessCrash()

    controller.backend = CrashingBackend()
    with pytest.raises(ProcessCrash):
        controller.step(state.run_id)
    saved = controller.store.load(state.run_id)
    assert saved.current.attempted and saved.current.job_id is None
    rebuilt = make_controller()
    restored = rebuilt.step(state.run_id)
    assert restored.stage == "wait"
    assert job_count(rebuilt.backend) == 1
    result = finish(rebuilt, state.run_id)
    assert result.outcome == "success"
    assert job_count(rebuilt.backend) == result.experiments


def test_authoritative_absence_can_retry_same_request(make_controller, task):
    controller = make_controller()
    original = controller.backend.submit
    calls = []

    def submit(request):
        calls.append(request.request_id)
        if len(calls) == 1:
            raise SubmissionUnknown()
        return original(request)

    controller.backend.submit = submit
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"
    assert calls[0] == calls[1]
    assert job_count(controller.backend) == state.experiments


def test_restart_after_data_fetch_does_not_submit_again(make_controller, task):
    controller = make_controller()
    run_id = controller.create(task).run_id
    for _ in range(3):
        state = controller.step(run_id)
    assert state.stage == "analyze"
    assert state.current_data_ref
    rebuilt = make_controller()
    state = rebuilt.step(run_id)
    assert state.stage == "decide"
    assert job_count(rebuilt.backend) == 1


def test_failed_experiments_are_charged_and_bounded(make_controller, task):
    controller = make_controller(SimulationSettings(duration_seconds=0, fail_jobs=True))
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed"
    assert state.experiments == task.max_errors
    assert state.used_measurements > 0
    assert state.reserved_measurements == 0


def test_no_target_does_not_report_success(make_controller, task):
    controller = make_controller(SimulationSettings(duration_seconds=0, contrast=0))
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome != "success"
    assert not any(v.passed for v in state.verifications)


def test_verification_failure_returns_to_measurement(make_controller, task):
    controller = make_controller(backend=ReplayBackend(verification_shift=4e6))
    state = finish(controller, controller.create(task.model_copy(update={"max_experiments": 5})).run_id)
    assert state.outcome == "not_converged"
    assert state.verifications and not any(v.passed for v in state.verifications)
    assert state.experiments > 3


def test_backend_can_be_replaced_without_controller_changes(make_controller, task):
    controller = make_controller(backend=ReplayBackend())
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"
    statuses = {e.get("status") for e in controller.store.events(state.run_id)}
    assert {"queued", "running"} <= statuses


def test_backend_result_provenance_is_checked(make_controller, task):
    backend = ReplayBackend()
    original = backend.fetch
    backend.fetch = lambda job: original(job).model_copy(update={"request_id": "foreign-request"})
    controller = make_controller(backend=backend)
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed"
    assert not state.analyses


def test_deadline_prevents_new_experiment(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    state.deadline_at = time.time() - 1
    controller.store.save(state, "test_deadline")
    state = controller.step(state.run_id)
    assert state.outcome == "not_converged"
    assert job_count(controller.backend) == 0


def test_run_lock_excludes_second_executor(make_controller, task):
    controller = make_controller()
    state = controller.create(task)
    with controller.store.run_lock(state.run_id):
        with pytest.raises(RunBusy):
            controller.step(state.run_id)


def test_resume_rejects_changed_backend(make_controller, task, tmp_path):
    controller = make_controller()
    state = controller.create(task)
    other = SimulatorBackend(tmp_path / "other.sqlite", SimulationSettings(duration_seconds=0))
    rebuilt = Controller(controller.store, other, FixedPolicy())
    with pytest.raises(ValueError, match="same backend"):
        rebuilt.step(state.run_id)


def test_agent_context_excludes_private_adapter_information(make_controller, task):
    controller = make_controller()
    state = controller.create(task, {"private_connection": "NOT_FOR_MODEL"})
    view = build_agent_view(state, controller.capabilities).model_dump_json()
    for key in ["true_frequency", "noise_std", "seed", "backend_identity", "NOT_FOR_MODEL"]:
        assert key not in view


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_policy_failure_is_bounded_and_does_not_leak_error_body(make_controller, task, error_type):
    class BrokenPolicy(BadPolicy):
        def choose(self, view):
            raise error_type("PRIVATE_API_RESPONSE")
    controller = make_controller(policy=BrokenPolicy())
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "failed"
    assert "PRIVATE_API_RESPONSE" not in state.model_dump_json()


def test_low_quality_measurements_change_sampling_strategy(make_controller, task):
    controller = make_controller(SimulationSettings(duration_seconds=0, contrast=0.04, noise_std=3))
    state = finish(controller, controller.create(task).run_id)
    shots = [d.scan.shots_per_point for d in state.decisions if d.scan]
    assert len(shots) >= 2 and max(shots) > shots[0]
    assert any(not a.trusted for a in state.analyses)


def test_query_errors_retry_existing_job_without_resubmitting(make_controller, task):
    controller = make_controller()
    original = controller.backend.status
    calls = []

    def status(job_id):
        calls.append(job_id)
        if len(calls) == 1:
            raise OSError("temporary query failure")
        return original(job_id)

    controller.backend.status = status
    state = finish(controller, controller.create(task).run_id)
    assert state.outcome == "success"
    assert calls[0] == calls[1]
    assert job_count(controller.backend) == state.experiments
