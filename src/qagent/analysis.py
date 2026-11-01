"""Derive spectra from IQ and fit measurements without adapter imports."""

import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

from .schemas import AnalysisResult, CalibrationTask, Measurement


ANALYSIS_VERSION = "spectroscopy_iq_dispatch_v2"


def derive_spectrum(measurement: Measurement) -> dict:
    """Average complex samples before calculating amplitude and phase.

    abs(mean(I) + 1j*mean(Q)) differs from mean(abs(I + 1j*Q)).
    Averaged input is already averaged by the backend; no shots are invented.
    """
    common = {"frequencies": measurement.frequencies, "x_unit": measurement.x_unit,
              "y_unit": measurement.y_unit, "data_type": measurement.data_type,
              "response_mode": measurement.response_mode, "request_id": measurement.request_id}
    if measurement.data_type == "probability":
        return {**common, "values": measurement.values}
    i = np.asarray(measurement.iq.i, dtype=float)
    q = np.asarray(measurement.iq.q, dtype=float)
    if measurement.iq.mode == "single_shot":
        i, q = i.mean(axis=1), q.mean(axis=1)
    amplitude = np.hypot(i, q)
    phase = np.unwrap(np.arctan2(q, i))
    if not np.all(np.isfinite(amplitude)) or not np.all(np.isfinite(phase)):
        raise ValueError("derived IQ spectrum must be finite")
    return {**common, "iq_mode": measurement.iq.mode, "mean_i": i.tolist(), "mean_q": q.tolist(),
            "amplitude": amplitude.tolist(), "phase_radians": phase.tolist()}


def analyze(measurement: Measurement, task: CalibrationTask, data_ref: str) -> AnalysisResult:
    is_iq = measurement.data_type == "iq"
    common = {"analysis_id": "analysis_" + measurement.request_id, "data_ref": data_ref,
              "data_type": measurement.data_type, "response_mode": measurement.response_mode,
              "method": "iq_amplitude_lorentzian_v2" if is_iq else "lorentzian_least_squares_v1"}
    x = np.asarray(measurement.frequencies, dtype=float)
    # Scale coordinates for numerical conditioning at GHz carrier frequencies.
    origin = float(x[0])
    scale = float(x[-1] - x[0])
    u = (x - origin) / scale
    spacing = float(np.max(np.diff(x)))
    sign = -1.0 if is_iq and measurement.response_mode == "dip" else 1.0

    def response(t, baseline, amplitude, center, width):
        return baseline + sign * amplitude * width**2 / ((t - center)**2 + width**2)

    try:
        spectrum = derive_spectrum(measurement)
        y = np.asarray(spectrum["amplitude"] if is_iq else spectrum["values"], dtype=float)
        baseline = float(np.median(y))
        if is_iq:
            # Keep physical units in artifacts. Normalize only fit coordinates
            # so microvolt and large ADC-scale signals are equally well scaled.
            span = float(np.ptp(y))
            if span <= np.finfo(float).eps * max(float(np.max(np.abs(y))), np.finfo(float).tiny):
                return AnalysisResult(**common, trusted=False, issue="flat_response")
            fit_y = (y - baseline) / span
            index = int(np.argmax(sign * fit_y))
            initial = [0.0, max(1e-5, float(sign * fit_y[index])), float(u[index]), 0.03]
            bounds = ([-np.inf, 0, 0, 1e-5], [np.inf, np.inf, 1, 0.5])
        else:
            fit_y = y
            initial = [max(0, baseline), max(1e-5, float(y.max() - baseline)), float(u[y.argmax()]), 0.03]
            bounds = ([0, 0, 0, 1e-5], [1, 2, 1, 0.5])
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            fit, covariance = curve_fit(response, u, fit_y, p0=initial, bounds=bounds, maxfev=10000)
        residual = fit_y - response(u, *fit)
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
