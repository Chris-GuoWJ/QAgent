"""Calibration rules and durable transitions. Depends only on public ports."""

import time
from uuid import uuid4

from .analysis import analyze, derive_spectrum
from .backend.base import ExperimentBackend, SubmissionRejected
from .policy import DecisionPolicy
from .schemas import CalibrationTask, Decision, ExperimentRequest, Measurement, RunState, ScanConfig, VerificationResult
from .state import build_agent_view
from .storage import Store


def _decision_failure_message(exc: Exception) -> str:
    """Keep useful HTTP diagnostics without retaining provider response bodies."""
    message = "Decision provider failed: " + type(exc).__name__
    # LangChain can wrap SDK status errors; inspect only numeric metadata from
    # the cause chain, never stringify exceptions or retain their bodies.
    status = None
    cause = exc
    for _ in range(4):
        status = getattr(cause, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            break
        cause = cause.__cause__
        if cause is None:
            break
    if type(status) is int and 100 <= status <= 599:
        message += f" (HTTP {status})"
        if status in {400, 422}:
            message += "; 模型服务拒绝请求参数，请检查决策输出模式与工具调用兼容性"
        elif status in {401, 403}:
            message += "; 模型服务鉴权或访问被拒绝，请在本机检查凭证与模型权限"
        elif status == 404:
            message += "; 模型或服务接口不存在，请在本机检查模型标识与接口地址"
        elif status == 429:
            message += "; 模型服务限制请求，请检查调用限额或稍后重试"
    return message


class Controller:
    def __init__(self, store: Store, backend: ExperimentBackend, policy: DecisionPolicy):
        self.store, self.backend, self.policy = store, backend, policy
        self.capabilities = backend.capabilities()

    def create(self, task: CalibrationTask, metadata: dict[str, str] | None = None) -> RunState:
        cap = self.capabilities
        if task.experiment_type not in cap.experiment_types:
            raise ValueError("Backend does not support the requested experiment")
        if not cap.lower_hz <= task.lower_hz < task.upper_hz <= cap.upper_hz:
            raise ValueError("Authorized task range exceeds backend capabilities")
        now = time.time()
        state = RunState(run_id=uuid4().hex, task=task, created_at=now,
            deadline_at=now + task.timeout_seconds, backend_identity=self.backend.identity,
            policy_identity=self.policy.identity, metadata=metadata or {})
        self.store.create(state)
        return state

    def step(self, run_id: str) -> RunState:
        """Execute one durable transition; useful for deterministic control tests."""
        with self.store.run_lock(run_id):
            self._advance(run_id)
        return self.store.load(run_id)

    def _advance(self, run_id: str):
        """Caller must hold the run lock (the graph driver does)."""
        state = self.store.load(run_id)
        if state.backend_identity != self.backend.identity or state.policy_identity != self.policy.identity:
            raise ValueError("Resume requires the same backend and policy configuration")
        if state.stage == "finished":
            return
        if state.stage == "reconcile":
            self._reconcile(state)
            return
        # Uncertain external effects remain reconcilable even after a deadline or stop.
        if state.current and state.current.attempted and not state.current.job_id:
            if self.store.stopped(run_id):
                self._reconcile(state)
                return
        if self.store.stopped(run_id):
            self._finish(state, "stopped", "User requested stop; existing backend tasks are not implicitly cancelled")
            return
        if time.time() >= state.deadline_at:
            self._finish(state, "not_converged", "Run deadline exhausted; any existing task is retained in the report")
            return
        getattr(self, "_" + state.stage)(state)

    def _finish(self, state, outcome, reason):
        if state.current and not state.current.attempted:
            state.reserved_measurements -= state.current.config.cost
            state.requests.append(state.current)
            state.current = None
        state.stage, state.outcome, state.reason = "finished", outcome, reason
        state.ended_at = time.time()
        payload = {"outcome": outcome, "reason": reason}
        if outcome == "success":
            payload["verification"] = state.verifications[-1].model_dump()
            payload["analysis"] = state.analyses[-1].model_dump()
        self.store.save(state, "run_finished", payload)

    def _error(self, state, message):
        state.errors += 1
        state.feedback = message
        if state.errors >= state.task.max_errors:
            self._finish(state, "failed", message + "; error limit reached")
        else:
            self.store.save(state, "error", {"message": message, "errors": state.errors})

    def _validate_scan(self, state, config):
        task, cap = state.task, self.capabilities
        if not task.lower_hz <= config.start_hz < config.stop_hz <= task.upper_hz:
            raise ValueError("scan exceeds the task's authorized frequency range")
        if not cap.lower_hz <= config.start_hz < config.stop_hz <= cap.upper_hz:
            raise ValueError("scan exceeds backend frequency range")
        if config.points > cap.max_points or config.shots_per_point > cap.max_shots_per_point:
            raise ValueError("scan exceeds backend sampling limits")

    def _decide(self, state):
        task = state.task
        available = task.max_measurements - state.used_measurements - state.reserved_measurements
        if state.experiments >= task.max_experiments or available < 9:
            self._finish(state, "not_converged", "Experiment or measurement budget exhausted")
            return
        try:
            raw = self.policy.choose(build_agent_view(state, self.capabilities))
        except Exception as exc:
            self._error(state, _decision_failure_message(exc))
            return
        try:
            decision = Decision.model_validate(raw.model_dump() if isinstance(raw, Decision) else raw)
            state.decisions.append(decision)
            known = {a.analysis_id for a in state.analyses}
            if not set(decision.evidence_ids).issubset(known):
                raise ValueError("decision cites unknown evidence")
            if decision.action == "finish":
                self._finish(state, "not_converged", decision.reason)
                return
            purpose = "search"
            if decision.action == "verify":
                candidate = next((a for a in state.analyses if a.analysis_id == decision.candidate_id), None)
                if (candidate is None or candidate.analysis_id != state.candidate_id or not candidate.trusted
                        or candidate.uncertainty_hz > task.target_uncertainty_hz):
                    raise ValueError("verification requires the current trusted candidate meeting target precision")
                if candidate.analysis_id not in decision.evidence_ids:
                    raise ValueError("verification must cite its candidate analysis")
                half = max(5 * candidate.linewidth_hz, 8 * candidate.uncertainty_hz)
                config = ScanConfig(start_hz=max(task.lower_hz, candidate.frequency_hz - half),
                    stop_hz=min(task.upper_hz, candidate.frequency_hz + half),
                    points=min(task.initial_points + 2, self.capabilities.max_points),
                    shots_per_point=min(task.initial_shots * 2, self.capabilities.max_shots_per_point))
                purpose = "verification"
            else:
                config = decision.scan
            self._validate_scan(state, config)
            if config.cost > available:
                self._finish(state, "not_converged", "Requested experiment exceeds remaining measurement budget")
                return
        except Exception as exc:
            # Schema error inputs can contain arbitrary model text; omit those bodies.
            message = str(exc) if isinstance(exc, ValueError) and not hasattr(exc, "errors") else type(exc).__name__
            self._error(state, "Decision rejected: " + message[:500])
            return
        state.current = ExperimentRequest(request_id=uuid4().hex, qubit=task.qubit,
            experiment_type=task.experiment_type, purpose=purpose, config=config,
            candidate_id=decision.candidate_id)
        state.current_data_ref = None
        state.reserved_measurements += config.cost
        state.experiments += 1
        state.stage, state.feedback = "submit", ""
        self.store.save(state, "decision_accepted", {"decision": decision.model_dump(),
            "request_id": state.current.request_id, "config": config.model_dump(), "purpose": purpose})

    def _submit(self, state):
        if state.current.attempted:
            # Could have crashed after the external side effect but before the receipt.
            self._reconcile(state)
            return
        if not self.store.begin_submission(state):
            self._finish(state, "stopped", "Stop requested before submission started")
            return
        try:
            job_id = self.backend.submit(state.current)
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("missing backend job receipt")
        except SubmissionRejected:
            state.reserved_measurements -= state.current.config.cost
            state.requests.append(state.current)
            state.current = None
            state.stage = "decide"
            self._error(state, "Backend explicitly rejected the experiment without accepting it")
            return
        except Exception:
            # Every non-authoritative submission error is ambiguous, including timeouts.
            state.stage = "reconcile"
            state.errors += 1
            state.feedback = "Submission outcome unknown; reconcile before any further submission"
            self.store.save(state, "submission_unknown")
            return
        self._accept(state, job_id)

    def _accept(self, state, job_id):
        state.current.job_id = job_id
        if not state.current.charged:
            state.reserved_measurements -= state.current.config.cost
            state.used_measurements += state.current.config.cost
            state.current.charged = True
        state.stage, state.feedback = "wait", ""
        self.store.save(state, "job_accepted", {"request_id": state.current.request_id, "job_id": job_id})

    def _reconcile(self, state):
        state.stage = "reconcile"
        try:
            lookup = self.backend.lookup(state.current.request_id) if self.capabilities.request_lookup else None
        except Exception:
            lookup = None
        if lookup is None or lookup.state == "unknown":
            state.feedback = "External request requires reconciliation; no new experiments will be submitted"
            self.store.save(state, "reconciliation_required", {"request_id": state.current.request_id})
            return
        if lookup.state == "found":
            if state.current.job_id and state.current.job_id != lookup.job_id:
                state.feedback = "Conflicting backend receipts require external reconciliation"
                self.store.save(state, "reconciliation_required")
                return
            self._accept(state, lookup.job_id)
            return
        # Absence must mean authoritative absence, not an eventually-consistent miss.
        if state.current.job_id or state.current.charged:
            state.feedback = "Previously accepted job cannot be found; external reconciliation required"
            self.store.save(state, "reconciliation_required")
            return
        state.current.attempted = False
        if self.store.stopped(state.run_id):
            self._finish(state, "stopped", "Stopped after confirming the request was not accepted")
        elif time.time() >= state.deadline_at or state.errors >= state.task.max_errors:
            self._finish(state, "not_converged", "No accepted job; deadline or submission error limit exhausted")
        else:
            state.stage = "submit"
            self.store.save(state, "request_confirmed_absent")

    def _wait(self, state):
        try:
            status = self.backend.status(state.current.job_id)
            if status.state in {"queued", "running"}:
                self.store.save(state, "job_progress", {"job_id": state.current.job_id, "status": status.state})
                return
            if status.state == "unknown":
                state.stage = "reconcile"
                self.store.save(state, "job_unknown")
                return
            if status.state == "failed":
                state.requests.append(state.current)
                state.current = None
                state.stage = "decide"
                self._error(state, "Experiment failed; accepted measurement budget remains charged")
                return
            raw = self.backend.fetch(state.current.job_id)
            measurement = Measurement.model_validate(raw.model_dump() if isinstance(raw, Measurement) else raw)
            current = state.current
            if (measurement.request_id != current.request_id or measurement.job_id != current.job_id
                    or measurement.qubit != current.qubit or measurement.experiment_type != current.experiment_type
                    or measurement.config != current.config or measurement.data_type != self.capabilities.data_type
                    or measurement.response_mode != self.capabilities.response_mode):
                raise ValueError("backend result does not match the accepted experiment")
            state.current_data_ref = self.store.write_artifact(state.run_id,
                current.request_id + ".json", measurement.model_dump())
            state.stage = "analyze"
            self.store.save(state, "data_received", {"data_ref": state.current_data_ref})
        except Exception as exc:
            self._error(state, "Status or result query failed: " + type(exc).__name__)

    def _analyze(self, state):
        measurement = Measurement.model_validate(self.store.read_artifact(state.current_data_ref))
        result = analyze(measurement, state.task, state.current_data_ref)
        try:
            spectrum = derive_spectrum(measurement)
            result.spectrum_ref = self.store.write_artifact(state.run_id,
                measurement.request_id + ".spectrum.json", spectrum)
        except (ValueError, FloatingPointError):
            # Always retain original IQ even when deriving the spectrum fails.
            pass
        state.analyses.append(result)
        current = state.current
        if current.purpose == "verification":
            candidate = next(a for a in state.analyses if a.analysis_id == current.candidate_id)
            difference = (abs(candidate.frequency_hz - result.frequency_hz)
                          if result.frequency_hz is not None else None)
            passed = bool(candidate.trusted and result.trusted
                and candidate.data_ref != result.data_ref
                and candidate.data_type == result.data_type
                and candidate.response_mode == result.response_mode
                and result.uncertainty_hz <= state.task.target_uncertainty_hz
                and difference <= state.task.verification_tolerance_hz)
            verification = VerificationResult(candidate_id=candidate.analysis_id, analysis_id=result.analysis_id,
                passed=passed, frequency_difference_hz=difference,
                reason="Independent measurement passed precision, quality and agreement criteria" if passed
                    else "Independent measurement failed quality, precision or agreement criteria")
            state.verifications.append(verification)
            state.requests.append(current)
            state.current = None
            if passed:
                self._finish(state, "success", verification.reason)
                return
            state.candidate_id = None
            state.feedback = verification.reason
        else:
            if result.trusted:
                state.candidate_id = result.analysis_id
            state.requests.append(current)
            state.current = None
            state.feedback = result.issue
        state.stage = "decide"
        self.store.save(state, "analysis_completed", {"analysis": result.model_dump()})

    def report(self, run_id: str) -> dict:
        state = self.store.load(run_id)
        candidate = next((a for a in state.analyses if a.analysis_id == state.candidate_id), None)
        report = {"run_id": run_id, "stage": state.stage, "outcome": state.outcome,
            "reason": state.reason or state.feedback, "task": state.task.model_dump(),
            "created_at": state.created_at, "ended_at": state.ended_at,
            "backend_identity": state.backend_identity, "policy_identity": state.policy_identity,
            "candidate": candidate.model_dump() if candidate else None,
            "analyses": [a.model_dump() for a in state.analyses],
            "experiments": [r.model_dump() for r in state.requests],
            "verification": [v.model_dump() for v in state.verifications],
            "budget": {"experiments": state.experiments, "used_measurements": state.used_measurements,
                       "reserved_measurements": state.reserved_measurements},
            "active_request": state.current.model_dump() if state.current else None,
            "decisions": [d.model_dump() for d in state.decisions], "metadata": state.metadata,
            "measurement_type": self.capabilities.data_type, "response_mode": self.capabilities.response_mode,
            "limitations": ["Single-qubit spectroscopy; a single IQ-amplitude peak/dip or explicit legacy probability peak is assumed",
                "Uncertainty is a fit estimate with a grid-resolution floor, not validated hardware accuracy",
                "Existing external jobs are not implicitly cancelled when execution stops"]}
        self.store.write_artifact(run_id, "report.json", report)
        return report
