"""A transparent backend decorator; no changes or monkey patches to QuantumX."""

from contextlib import contextmanager

import numpy as np

from .results import write_array


class ExperimentInterrupted(RuntimeError):
    pass


class RecordingBackend:
    def __init__(self, backend, store, namespace, labels, stop_event=None, expected_shots=None):
        self.backend, self.store, self.namespace = backend, store, namespace
        self.labels = labels
        self.stop_event = stop_event
        self.expected_shots = expected_shots
        self.records = []

    @contextmanager
    def session(self):
        with self.backend.session():
            yield self

    def run(self, pulse_ir, shots):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ExperimentInterrupted("Experiment interrupted at a call boundary")
        index = len(self.records)
        if index >= len(self.labels):
            raise ValueError("Function made more acquisition calls than its declared scan plan")
        if self.expected_shots is not None and shots != self.expected_shots:
            raise ValueError("Function shots differ from its reserved measurement plan")
        raw = self.backend.run(pulse_ir, shots)
        array = np.asarray(raw)
        if array.shape != (shots, 2) or not np.issubdtype(array.dtype, np.number) or np.iscomplexobj(array):
            raise ValueError("QuantumX backend must return real IQ with shape (shots, 2)")
        if not np.all(np.isfinite(array)):
            raise ValueError("IQ samples must be finite")
        reference = write_array(self.store, self.namespace, f"iq-{index:04d}.npy", array)
        ir_ref = self.store.write_artifact(self.namespace, f"pulse-{index:04d}.json", pulse_ir.to_dict())
        self.records.append({"call_index": index, "shots": int(shots), "shape": list(array.shape),
                             "data_type": "iq", "iq_mode": "single_shot", "y_unit": "a.u.",
                             "labels": self.labels[index], "data_ref": reference, "pulse_ir_ref": ir_ref})
        self.store.write_artifact(self.namespace, "acquisitions.json", {"iq_records": self.records})
        return raw
