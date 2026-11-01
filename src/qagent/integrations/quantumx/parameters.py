from pathlib import Path

from pydantic import Field, model_validator

from ...schemas import Model


class QuantumXSettings(Model):
    source_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[5] / "QuantumX2")
    hardware_config: Path | None = None
    qubits_config: Path | None = None
    allowed_qubits: list[int] = Field(default_factory=lambda: [1, 2])
    max_experiments: int = Field(default=8, ge=1, le=100)
    max_measurements: int = Field(default=500_000, ge=1)
    simulation_seed: int = 42

    @model_validator(mode="after")
    def targets(self):
        if not self.allowed_qubits or any(type(n) is not int or n < 1 for n in self.allowed_qubits):
            raise ValueError("allowed_qubits must contain positive integer indices")
        if len(self.allowed_qubits) != len(set(self.allowed_qubits)):
            raise ValueError("allowed_qubits must be unique")
        return self


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
