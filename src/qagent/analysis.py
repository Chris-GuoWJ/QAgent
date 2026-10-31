"""Analysis of public measurements; never imports an experiment adapter."""

import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

from .schemas import AnalysisResult, CalibrationTask, Measurement


def analyze(measurement: Measurement, task: CalibrationTask, data_ref: str) -> AnalysisResult:
    analysis_id = "analysis_" + measurement.request_id
    common = {"analysis_id": analysis_id, "data_ref": data_ref}
    x = np.asarray(measurement.frequencies, dtype=float)
    y = np.asarray(measurement.values, dtype=float)
    # Scale coordinates for numerical conditioning at GHz carrier frequencies.
    origin = float(x[0])
    scale = float(x[-1] - x[0])
    u = (x - origin) / scale
    spacing = float(np.max(np.diff(x)))

    def response(t, baseline, amplitude, center, width):
        return baseline + amplitude * width**2 / ((t - center)**2 + width**2)

    try:
        baseline = float(np.median(y))
        initial = [max(0, baseline), max(1e-5, float(y.max() - baseline)), float(u[y.argmax()]), 0.03]
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            fit, covariance = curve_fit(response, u, y, p0=initial,
                bounds=([0, 0, 0, 1e-5], [1, 2, 1, 0.5]), maxfev=10000)
        residual = y - response(u, *fit)
        noise = max(float(np.std(residual, ddof=4)), 1e-10)
        snr = float(fit[1] / noise)
        frequency = float(origin + fit[2] * scale)
        uncertainty = max(float(np.sqrt(covariance[2, 2]) * scale), spacing / np.sqrt(12))
        linewidth = float(fit[3] * scale)
        finite = all(np.isfinite(value) for value in [snr, frequency, uncertainty, linewidth])
        interior = x[2] < frequency < x[-3]
        resolved = linewidth >= spacing / 2 and linewidth < scale / 4
        trusted = bool(finite and interior and resolved and snr >= task.min_snr)
        if not finite:
            raise ValueError("non-finite fit uncertainty")
        issues = []
        if snr < task.min_snr:
            issues.append("low_snr")
        if not interior:
            issues.append("peak_at_boundary")
        if not resolved:
            issues.append("unresolved_width")
        return AnalysisResult(**common, trusted=trusted, frequency_hz=frequency,
            uncertainty_hz=float(uncertainty), linewidth_hz=linewidth, snr=snr, issue=",".join(issues))
    except (ValueError, RuntimeError, OptimizeWarning, FloatingPointError):
        return AnalysisResult(**common, trusted=False, issue="fit_failed")
