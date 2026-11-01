"""Explicit synthetic IQ for function integration; never creates ZwBackend."""

from contextlib import contextmanager

import numpy as np


class SimulatedFunctionBackend:
    def __init__(self, context):
        self.context = context
        self.rng = np.random.default_rng(context["seed"])
        self.index = 0
        self.active = False

    @contextmanager
    def session(self):
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    def run(self, pulse_ir, shots):
        if not self.active:
            raise RuntimeError("Acquisition requires a session")
        context = self.context
        label = context["labels"][self.index]
        original = context["original_qubit"]
        current = context["qubit"]
        centers = original.readoutCenters
        c0 = np.asarray(centers.get(0, (-1.0, 0.0)), dtype=float)
        c1 = np.asarray(centers.get(1, (1.0, 0.0)), dtype=float)
        if context["experiment_type"] == "readout_centers":
            p1 = float(label["state"])
        elif context["experiment_type"] == "pi_amplitude":
            true_pi = original.piAmp * 1.02
            p1 = float(np.sin(np.pi * label["amplitude"] / (2 * true_pi))**2)
        else:
            t = label["delay_s"] - context["parameters"]["delay_start_s"]
            true_f10 = original.f10 + 100e3
            f = context["parameters"]["fringe_freq_hz"] - (true_f10 - current.f10)
            angle = 2 * np.pi * f * t
            oscillation = np.cos(angle) if label["series"] == "X" else -np.sin(angle)
            p1 = float(0.5 + 0.45 * np.exp(-t / 20e-6) * oscillation)
        labels = self.rng.random(shots) < p1
        array = np.where(labels[:, None], c1, c0)
        noise = max(float(np.linalg.norm(c1 - c0)), 1e-6) * 0.01
        array = array + self.rng.normal(0, noise, (shots, 2))
        self.index += 1
        return array
