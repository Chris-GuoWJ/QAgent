"""Explicit allowlist of facts made visible to a decision policy."""

from .schemas import AnalysisResult, CalibrationTask, Capabilities, Model, RunState, ScanConfig, VerificationResult


class AgentView(Model):
    task: CalibrationTask
    capabilities: Capabilities
    remaining_experiments: int
    remaining_measurements: int
    candidate_id: str | None
    analyses: list[AnalysisResult]
    verifications: list[VerificationResult]
    previous_scan: ScanConfig | None
    feedback: str


def build_agent_view(state: RunState, capabilities: Capabilities) -> AgentView:
    previous = state.requests[-1].config if state.requests else None
    # Deliberately omit backend identity, connection configuration, raw artifacts,
    # runtime metadata, private adapter settings, and the storage filesystem.
    return AgentView(task=state.task, capabilities=capabilities,
        remaining_experiments=state.task.max_experiments - state.experiments,
        remaining_measurements=state.task.max_measurements - state.used_measurements - state.reserved_measurements,
        candidate_id=state.candidate_id, analyses=state.analyses[-8:],
        verifications=state.verifications[-8:], previous_scan=previous, feedback=state.feedback)
