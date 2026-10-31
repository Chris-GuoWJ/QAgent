"""Policy port and deterministic baseline; neither imports concrete backends."""

from typing import Protocol

from .schemas import Decision, ScanConfig
from .state import AgentView


class DecisionPolicy(Protocol):
    @property
    def identity(self) -> str: ...

    def choose(self, view: AgentView) -> Decision: ...


class FixedPolicy:
    identity = "fixed_spectroscopy_v1"

    def choose(self, view: AgentView):
        task = view.task
        if not view.analyses:
            return Decision(action="scan", scan=ScanConfig(start_hz=task.lower_hz, stop_hz=task.upper_hz,
                points=task.initial_points, shots_per_point=task.initial_shots), reason="Measure the authorized range")
        latest = view.analyses[-1]
        evidence = [latest.analysis_id]
        if latest.trusted:
            if latest.uncertainty_hz <= task.target_uncertainty_hz and view.candidate_id == latest.analysis_id:
                return Decision(action="verify", candidate_id=view.candidate_id, evidence_ids=evidence,
                                reason="Test the candidate with an independent measurement")
            half = max(5 * latest.linewidth_hz, 5 * latest.uncertainty_hz)
            scan = ScanConfig(start_hz=max(task.lower_hz, latest.frequency_hz - half),
                stop_hz=min(task.upper_hz, latest.frequency_hz + half),
                points=task.initial_points, shots_per_point=task.initial_shots)
            return Decision(action="scan", scan=scan, evidence_ids=evidence, reason="Resolve the observed peak more precisely")
        previous = view.previous_scan
        shots = min(previous.shots_per_point * 4, view.capabilities.max_shots_per_point)
        if shots == previous.shots_per_point or previous.points * shots > view.remaining_measurements:
            return Decision(action="finish", evidence_ids=evidence, reason="Insufficient budget for another quality improvement")
        return Decision(action="scan", scan=previous.model_copy(update={"shots_per_point": shots}),
                        evidence_ids=evidence, reason="Improve measurement quality before accepting a candidate")
