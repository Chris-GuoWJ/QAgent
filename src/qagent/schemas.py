"""Public experiment contracts; no simulation or model-provider configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class ScanConfig(Model):
    start_hz: float = Field(gt=0)
    stop_hz: float = Field(gt=0)
    points: int = Field(ge=9, le=5001, strict=True)
    shots_per_point: int = Field(ge=1, le=1_000_000, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.start_hz >= self.stop_hz:
            raise ValueError("start_hz must be below stop_hz")
        return self

    @property
    def cost(self) -> int:
        return self.points * self.shots_per_point


class CalibrationTask(Model):
    qubit: str = Field(min_length=1, max_length=100)
    experiment_type: Literal["spectroscopy"] = "spectroscopy"
    lower_hz: float = Field(gt=0)
    upper_hz: float = Field(gt=0)
    initial_points: int = Field(default=81, ge=9, le=5001, strict=True)
    initial_shots: int = Field(default=256, ge=1, le=1_000_000, strict=True)
    max_experiments: int = Field(default=8, ge=1, le=100, strict=True)
    max_measurements: int = Field(default=500_000, ge=1, strict=True)
    timeout_seconds: float = Field(default=120, gt=0)
    max_errors: int = Field(default=3, ge=1, le=20, strict=True)
    target_uncertainty_hz: float = Field(default=300_000, gt=0)
    verification_tolerance_hz: float = Field(default=1_000_000, gt=0)
    min_snr: float = Field(default=6, gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.lower_hz >= self.upper_hz:
            raise ValueError("lower_hz must be below upper_hz")
        return self


class Decision(Model):
    action: Literal["scan", "verify", "finish"]
    scan: ScanConfig | None = None
    candidate_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def action_arguments(self):
        if self.action == "scan" and (self.scan is None or self.candidate_id is not None):
            raise ValueError("scan requires scan config and no candidate_id")
        if self.action == "verify" and (not self.candidate_id or self.scan is not None):
            raise ValueError("verify requires candidate_id and no scan config")
        if self.action == "finish" and (self.scan is not None or self.candidate_id is not None):
            raise ValueError("finish cannot include execution arguments")
        return self


class Capabilities(Model):
    experiment_types: list[str] = Field(default_factory=lambda: ["spectroscopy"])
    lower_hz: float = Field(gt=0)
    upper_hz: float = Field(gt=0)
    max_points: int = Field(default=5001, ge=9)
    max_shots_per_point: int = Field(default=1_000_000, ge=1)
    request_lookup: bool = False
    submission_deduplication: bool = False


class ExperimentRequest(Model):
    request_id: str
    qubit: str
    experiment_type: str
    purpose: Literal["search", "verification"]
    config: ScanConfig
    candidate_id: str | None = None
    attempted: bool = False
    job_id: str | None = None
    charged: bool = False


class JobStatus(Model):
    state: Literal["queued", "running", "completed", "failed", "unknown"]
    message: str = ""


class LookupResult(Model):
    state: Literal["found", "absent", "unknown"]
    job_id: str | None = None

    @model_validator(mode="after")
    def job_required(self):
        if (self.state == "found") != (self.job_id is not None):
            raise ValueError("only found lookups must contain a job_id")
        return self


class Measurement(Model):
    request_id: str
    job_id: str
    qubit: str
    experiment_type: str
    config: ScanConfig
    x_unit: Literal["Hz"] = "Hz"
    y_unit: Literal["probability"] = "probability"
    frequencies: list[float]
    values: list[float]

    @model_validator(mode="after")
    def arrays(self):
        if len(self.frequencies) != self.config.points or len(self.values) != self.config.points:
            raise ValueError("measurement lengths must match config.points")
        if any(a >= b for a, b in zip(self.frequencies, self.frequencies[1:])):
            raise ValueError("frequencies must be strictly increasing")
        if self.frequencies[0] < self.config.start_hz or self.frequencies[-1] > self.config.stop_hz:
            raise ValueError("measurement grid exceeds requested range")
        if any(not 0 <= value <= 1 for value in self.values):
            raise ValueError("probability values must be between 0 and 1")
        return self


class AnalysisResult(Model):
    analysis_id: str
    data_ref: str
    method: str = "lorentzian_least_squares_v1"
    trusted: bool
    frequency_hz: float | None = None
    uncertainty_hz: float | None = None
    linewidth_hz: float | None = None
    snr: float | None = None
    issue: str = ""


class VerificationResult(Model):
    candidate_id: str
    analysis_id: str
    passed: bool
    frequency_difference_hz: float | None = None
    reason: str


class RunState(Model):
    run_id: str
    task: CalibrationTask
    backend_identity: str
    policy_identity: str
    created_at: float
    deadline_at: float
    ended_at: float | None = None
    stage: Literal["decide", "submit", "wait", "analyze", "reconcile", "finished"] = "decide"
    outcome: Literal["success", "not_converged", "failed", "stopped"] | None = None
    reason: str = ""
    experiments: int = 0
    used_measurements: int = 0
    reserved_measurements: int = 0
    errors: int = 0
    feedback: str = ""
    current: ExperimentRequest | None = None
    current_data_ref: str | None = None
    candidate_id: str | None = None
    analyses: list[AnalysisResult] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    requests: list[ExperimentRequest] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
