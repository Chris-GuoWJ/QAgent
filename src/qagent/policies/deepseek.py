"""DeepSeek decisions via LangChain; receives only the public AgentView."""

import hashlib
import json
import os

from pydantic import Field

from ..schemas import Decision, Model


INSTRUCTIONS = """You choose the next action for single-qubit spectroscopy calibration.
Return one JSON decision matching the provided schema. Use only supplied evidence.
Actions: scan (supply config), verify (supply candidate_id), finish (no execution arguments).
Use Hz. Respect the task, backend capabilities and remaining budgets.
Analyze quality before precision. Refine a credible peak; increase shots for low SNR.
Verify only an existing trusted candidate meeting target uncertainty.
Independent verification is evaluated by the controller; never invent success or evidence.
Reference analysis_id or verification analysis_id values in evidence_ids.
Omit unused optional execution arguments or set them to null.
Provide a short reason, not private reasoning. Do not request filesystem or shell access.
"""


class DeepSeekSettings(Model):
    model: str = Field(min_length=1)
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    output_method: str = "function_calling"
    thinking: str | None = None


class DeepSeekPolicy:
    def __init__(self, settings: DeepSeekSettings, runnable=None):
        if settings.output_method not in {"function_calling", "json_mode"}:
            raise ValueError("output_method must be function_calling or json_mode")
        if settings.thinking not in {None, "enabled", "disabled"}:
            raise ValueError("thinking must be enabled or disabled")
        fingerprint = hashlib.sha256((settings.model_dump_json() + INSTRUCTIONS).encode()).hexdigest()[:20]
        self.identity = "deepseek:" + fingerprint
        self._instructions = INSTRUCTIONS + "\nDecision JSON schema:\n" + json.dumps(Decision.model_json_schema())
        if runnable is None:
            from langchain_deepseek import ChatDeepSeek

            key = os.environ.get(settings.api_key_env)
            if not key:
                raise ValueError(f"Set environment variable {settings.api_key_env} before using the DeepSeek policy")
            params = dict(model=settings.model, api_key=key, api_base=settings.base_url,
                          timeout=settings.timeout_seconds, max_retries=0)
            if settings.thinking:
                params["extra_body"] = {"thinking": {"type": settings.thinking}}
            model = ChatDeepSeek(**params)
            runnable = model.with_structured_output(Decision, method=settings.output_method, include_raw=True)
        self._runnable = runnable

    def choose(self, view):
        response = self._runnable.invoke([
            ("system", self._instructions), ("human", view.model_dump_json())])
        if isinstance(response, Decision):
            return response
        if isinstance(response, dict) and "parsed" in response:
            if response.get("parsing_error") or response["parsed"] is None:
                raise ValueError("DeepSeek response did not match the decision schema")
            response = response["parsed"]
        return response if isinstance(response, Decision) else Decision.model_validate(response)
