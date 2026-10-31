import math

import pytest

from qagent.backend.simulator import SimulationSettings, SimulatorBackend
from qagent.controller import Controller
from qagent.policy import FixedPolicy
from qagent.schemas import CalibrationTask, Capabilities, JobStatus, LookupResult, Measurement
from qagent.storage import Store


@pytest.fixture(autouse=True)
def protect_user_environment_files(monkeypatch):
    """No test or child CLI process may open the user's real credential file."""
    monkeypatch.setenv("QAGENT_NO_ENV_FILE", "1")


class ReplayBackend:
    """An independent protocol implementation, with no simulator inheritance."""

    identity = "replay-fixture-v1"

    def __init__(self, verification_shift=0):
        self.jobs = {}
        self.polls = {}
        self.verification_shift = verification_shift

    def capabilities(self):
        return Capabilities(lower_hz=4e9, upper_hz=6e9, request_lookup=True, submission_deduplication=True)

    def submit(self, request):
        if request.request_id in self.jobs:
            old = self.jobs[request.request_id]
            if old[1].config != request.config:
                raise ValueError("request id conflict")
            return old[0]
        job_id = f"receipt-{len(self.jobs)}"
        self.jobs[request.request_id] = (job_id, request.model_copy(deep=True))
        self.polls[job_id] = 0
        return job_id

    def lookup(self, request_id):
        row = self.jobs.get(request_id)
        return LookupResult(state="found", job_id=row[0]) if row else LookupResult(state="absent")

    def status(self, job_id):
        if job_id not in self.polls:
            return JobStatus(state="unknown")
        self.polls[job_id] += 1
        return JobStatus(state=["queued", "running", "completed"][min(self.polls[job_id] - 1, 2)])

    def fetch(self, job_id):
        request = next(req for job, req in self.jobs.values() if job == job_id)
        config = request.config
        center = 5.03e9 + (self.verification_shift if request.purpose == "verification" else 0)
        x = [config.start_hz + i * (config.stop_hz - config.start_hz) / (config.points - 1)
             for i in range(config.points)]
        y = [0.1 + 0.65 * (5e6)**2 / ((f - center)**2 + (5e6)**2) + 0.001 * math.sin(i)
             for i, f in enumerate(x)]
        return Measurement(request_id=request.request_id, job_id=job_id, qubit=request.qubit,
            experiment_type=request.experiment_type, config=config, frequencies=x, values=y)


@pytest.fixture
def task():
    return CalibrationTask(qubit="Q0", lower_hz=4.8e9, upper_hz=5.2e9)


@pytest.fixture
def make_controller(tmp_path):
    def factory(settings=None, policy=None, backend=None):
        port = backend or SimulatorBackend(tmp_path / "backend.sqlite", settings or SimulationSettings(duration_seconds=0))
        return Controller(Store(tmp_path / "runs"), port, policy or FixedPolicy())
    return factory


def finish(controller, run_id, limit=120):
    for _ in range(limit):
        state = controller.step(run_id)
        if state.stage == "finished":
            return state
        if controller.store.events(run_id)[-1]["event"] == "reconciliation_required":
            return state
    raise AssertionError("controller did not reach a terminal or reconciliation state")
