"""IQ fixture for offline tests only. Never installed as a runtime backend."""
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from qagent.integrations.quantumx.parameters import QuantumXSettings
from qagent.integrations.quantumx.service import QuantumXService
from qagent.storage import Store

SOURCE = Path(__file__).resolve().parents[2] / "QuantumX2"


class FixtureIQBackend:
    def __init__(self, context):
        self.context = context
        self.index = 0
        self.active = False
        self.rng = np.random.default_rng(42)

    @contextmanager
    def session(self):
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    def run(self, pulse_ir, shots):
        assert self.active
        c = self.context
        label = c["labels"][self.index]
        original, current = c["original_qubit"], c["qubit"]
        c0, c1 = np.array(original.readoutCenters[0]), np.array(original.readoutCenters[1])
        if c["experiment_type"] == "readout_centers":
            p1 = float(label["state"])
        elif c["experiment_type"] == "pi_amplitude":
            p1 = np.sin(np.pi * label["amplitude"] / (2 * original.piAmp * 1.02))**2
        else:
            t = label["delay_s"] - c["parameters"]["delay_start_s"]
            f = c["parameters"]["fringe_freq_hz"] - (original.f10 + 100e3 - current.f10)
            angle = 2 * np.pi * f * t
            oscillation = np.cos(angle) if label["series"] == "X" else -np.sin(angle)
            p1 = .5 + .45 * np.exp(-t / 20e-6) * oscillation
        labels = self.rng.random(shots) < p1
        raw = np.where(labels[:, None], c1, c0)
        self.index += 1
        return raw + self.rng.normal(0, np.linalg.norm(c1 - c0) * .01, (shots, 2))


def make_service(tmp_path, *, backend_factory=FixtureIQBackend, settings=None, on_event=None):
    values = dict(source_dir=SOURCE, hardware_config=SOURCE / "configs/hardware_tmpl.json",
                  qubits_config=SOURCE / "configs/qubits_tmpl.json")
    values.update(settings or {})
    return QuantumXService(Store(tmp_path / "runs", result_root=tmp_path / "result", config_root=tmp_path / "config/history"),
                           QuantumXSettings(**values), backend_factory, on_event=on_event)
