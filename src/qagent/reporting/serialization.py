"""Atomic array artifacts and finite JSON conversion for experiment results."""

import math
import os
from uuid import uuid4

import numpy as np


def write_array(store, namespace, filename, array):
    directory = store._path(store.result_root, namespace)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("wb") as stream:
            np.save(stream, np.asarray(array), allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.relative_to(store.result_root).as_posix()


def json_ready(value, warnings, path="result"):
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist(), warnings, path)
    if isinstance(value, np.generic):
        return json_ready(value.item(), warnings, path)
    if isinstance(value, float) and not math.isfinite(value):
        warnings.append(path + ": non-finite value stored as null")
        return None
    if isinstance(value, dict):
        return {str(k): json_ready(v, warnings, path + "." + str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v, warnings, f"{path}[{i}]") for i, v in enumerate(value)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported result value: " + type(value).__name__)


def summarize(name, result):
    if name == "readout_centers":
        metrics = {key: result[key] for key in ("center0", "center1", "fidelity", "training_shots", "validation_shots")}
        accepted = float(result["fidelity"]["avg"]) >= 0.8
    elif name == "pi_amplitude":
        metrics = {key: result[key] for key in ("best_piAmp", "contrast", "r_squared")}
        accepted = float(result["r_squared"]) >= 0.8 and float(result["contrast"]) >= 0.1
    else:
        metrics = {key: result[key] for key in ("best_f10", "correction", "T2Star", "contrast", "r_squared")}
        accepted = float(result["r_squared"]) >= 0.8 and float(result["contrast"]) >= 0.1
    warnings = []
    metrics = json_ready(metrics, warnings, "metrics")
    return metrics, {"accepted": accepted, "status": "candidate" if accepted else "needs_review",
                     "independently_verified": False}, warnings
