from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from qagent.core.models import Model


class ExperimentLimits(Model):
    allowed_qubits: list[int] = Field(default_factory=lambda: [1, 2])
    max_experiments: int = Field(default=8, ge=1, le=100)
    max_measurements: int = Field(default=500_000, ge=1)

    @model_validator(mode="after")
    def targets(self):
        if not self.allowed_qubits or any(type(n) is not int or n < 1 for n in self.allowed_qubits):
            raise ValueError("allowed_qubits must contain positive integer indices")
        if len(self.allowed_qubits) != len(set(self.allowed_qubits)):
            raise ValueError("allowed_qubits must be unique")
        return self


class QuantumXSettings(ExperimentLimits):
    source_dir: Path
    hardware_config: Path
    qubits_config: Path
    driver_dir: Path | None = None
    parameter_files: dict[str, Path] = Field(default_factory=dict)


class CommonParameters(Model):
    n: int = Field(default=1, ge=1, strict=True)
    shots: int = Field(default=512, ge=10, le=10_000, strict=True)
    reference_experiment_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ReadoutCentersParameters(CommonParameters):
    validation_fraction: float = Field(default=0.2, gt=0, le=0.5)


class RabiParameters(CommonParameters):
    amp_start: float = Field(default=0.0, ge=0, le=1)
    amp_stop: float | None = Field(default=None, gt=0, le=1)
    points: int = Field(default=41, ge=7, le=201, strict=True)

    @model_validator(mode="after")
    def scan(self):
        if self.amp_start != 0:
            raise ValueError("Rabi scan must start at zero to identify the first pi branch")
        if self.amp_stop is not None and self.amp_stop <= self.amp_start:
            raise ValueError("amp_stop must exceed amp_start")
        return self


class RamseyParameters(CommonParameters):
    delay_start_s: float = Field(default=0, ge=0)
    delay_stop_s: float = Field(default=1e-6, gt=0, le=100e-6)
    points: int = Field(default=31, ge=7, le=201, strict=True)
    fringe_freq_hz: float = 2e6
    nfftpoints: int = Field(default=4096, ge=64, le=65536, strict=True)

    @model_validator(mode="after")
    def scan(self):
        if self.delay_stop_s <= self.delay_start_s:
            raise ValueError("delay_stop_s must exceed delay_start_s")
        nyquist = 0.5 * (self.points - 1) / (self.delay_stop_s - self.delay_start_s)
        if abs(self.fringe_freq_hz) >= nyquist:
            raise ValueError("fringe_freq_hz must be below the delay-grid Nyquist frequency")
        return self


class SpectroscopyParameters(CommonParameters):
    points: int = Field(default=41, ge=7, le=201, strict=True)
    freq_start_hz: float | None = Field(default=None, ge=1, description="Start frequency in Hz")
    freq_stop_hz: float | None = Field(default=None, ge=1, description="Stop frequency in Hz")
    amps: list[Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=lambda: [0.02], min_length=1, max_length=16, description="Increasing normalized drive amplitudes")
    duration_s: float = Field(default=10e-6, gt=0, le=100e-6)
    envelope: Literal["raised_cosine", "square"] = "raised_cosine"

    @model_validator(mode="after")
    def scan(self):
        if (self.freq_start_hz is not None and self.freq_stop_hz is not None
                and self.freq_stop_hz <= self.freq_start_hz):
            raise ValueError("freq_stop_hz must exceed freq_start_hz")
        if self.amps[-1] <= 0 or any(a >= b for a, b in zip(self.amps, self.amps[1:])):
            raise ValueError("amps must increase strictly and include a positive amplitude")
        return self


class DelayParameters(CommonParameters):
    points: int = Field(default=31, ge=7, le=201, strict=True)
    delay_start_s: float = Field(default=0.0, ge=0, le=100e-6)
    delay_stop_s: float = Field(default=10e-6, gt=0, le=100e-6)

    @model_validator(mode="after")
    def scan(self):
        if self.delay_stop_s <= self.delay_start_s:
            raise ValueError("delay_stop_s must exceed delay_start_s")
        return self


class EchoParameters(DelayParameters):
    phase: float = Field(default=0.0, description="Analysis phase in rad")


class XYZDelayParameters(DelayParameters):
    delay_start_s: float = Field(default=-60e-9, ge=-100e-6, le=100e-6)
    delay_stop_s: float = Field(default=60e-9, ge=-100e-6, le=100e-6)
    z_amp: float = Field(ge=-1, le=1, description="Nonzero normalized Z-pulse amplitude")
    z_duration_s: float = Field(gt=0, le=100e-6, description="Z-pulse duration in seconds")

    @model_validator(mode="after")
    def nonzero_pulse(self):
        if self.z_amp == 0:
            raise ValueError("z_amp must be nonzero")
        return self


class PiHalfParameters(CommonParameters):
    points: int = Field(default=21, ge=7, le=201, strict=True)
    amp_start: float | None = Field(default=None, gt=0, le=1)
    amp_stop: float | None = Field(default=None, gt=0, le=1)
    phase_points: int = Field(default=16, ge=4, le=64, strict=True)

    @model_validator(mode="after")
    def scan(self):
        if self.amp_start is not None and self.amp_stop is not None and self.amp_start >= self.amp_stop:
            raise ValueError("amp_stop must exceed amp_start")
        return self


class DragParameters(CommonParameters):
    points: int = Field(default=21, ge=7, le=201, strict=True)
    alpha_start_s: float | None = None
    alpha_stop_s: float | None = None
    repetitions: list[Annotated[int, Field(ge=0, le=64, strict=True)]] = Field(
        default_factory=lambda: [1, 3, 7], min_length=2, max_length=16)

    @model_validator(mode="after")
    def scan(self):
        if (self.alpha_start_s is not None and self.alpha_stop_s is not None
                and self.alpha_start_s >= self.alpha_stop_s):
            raise ValueError("alpha_stop_s must exceed alpha_start_s")
        if any(a >= b for a, b in zip(self.repetitions, self.repetitions[1:])):
            raise ValueError("repetitions must increase strictly")
        return self
