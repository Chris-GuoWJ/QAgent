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
    data_type: Literal["iq", "probability"] = "iq"
    response_mode: Literal["peak", "dip"] = "dip"


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


class IQData(Model):
    """Averaged i/q[frequency], or single-shot i/q[frequency][shot]."""

    mode: Literal["averaged", "single_shot"] = "averaged"
    i: list[float] | list[list[float]]
    q: list[float] | list[list[float]]


class Measurement(Model):
    request_id: str
    job_id: str
    qubit: str
    experiment_type: str
    config: ScanConfig
    x_unit: Literal["Hz"] = "Hz"
    schema_version: Literal[1, 2] = 2
    data_type: Literal["iq", "probability"] = "iq"
    y_unit: Literal["a.u.", "V", "probability"] = "a.u."
    response_mode: Literal["peak", "dip"] = "dip"
    frequencies: list[float]
    iq: IQData | None = None
    values: list[float] | None = None

    @model_validator(mode="before")
    @classmethod
    def legacy_probability(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            # Persisted V1 files explicitly label probability units. Never
            # guess the meaning of an unlabelled scalar response array.
            if "data_type" not in data and data.get("y_unit") == "probability":
                data["data_type"] = "probability"
                data.setdefault("schema_version", 1)
            if data.get("data_type") == "probability":
                data.setdefault("y_unit", "probability")
                data.setdefault("response_mode", "peak")
        return data

    @model_validator(mode="after")
    def arrays(self):
        if len(self.frequencies) != self.config.points:
            raise ValueError("measurement frequencies must match config.points")
        if any(a >= b for a, b in zip(self.frequencies, self.frequencies[1:])):
            raise ValueError("frequencies must be strictly increasing")
        if self.frequencies[0] < self.config.start_hz or self.frequencies[-1] > self.config.stop_hz:
            raise ValueError("measurement grid exceeds requested range")
        if self.data_type == "probability":
            if self.iq is not None or self.y_unit != "probability" or self.response_mode != "peak":
                raise ValueError("legacy probability data require probability units, peak mode, and no IQ")
            if self.values is None or len(self.values) != self.config.points:
                raise ValueError("probability values must match config.points")
            if any(not 0 <= value <= 1 for value in self.values):
                raise ValueError("probability values must be between 0 and 1")
        else:
            if self.schema_version != 2 or self.y_unit == "probability" or self.iq is None or self.values is not None:
                raise ValueError("IQ measurements require V2, IQ units and iq channels, without probability values")
            for channel in (self.iq.i, self.iq.q):
                if len(channel) != self.config.points:
                    raise ValueError("IQ channels must match config.points")
                if self.iq.mode == "averaged":
                    if any(isinstance(value, list) for value in channel):
                        raise ValueError("averaged IQ channels must have shape (points,)")
                elif any(not isinstance(row, list) or len(row) != self.config.shots_per_point for row in channel):
                    raise ValueError("single-shot IQ channels must have shape (points, shots_per_point)")
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
    # Missing V1 fields retain the meaning of probability analysis records.
    data_type: Literal["iq", "probability"] = "probability"
    response_mode: Literal["peak", "dip"] = "peak"
    spectrum_ref: str | None = None


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
