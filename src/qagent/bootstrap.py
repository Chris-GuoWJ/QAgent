"""The only composition root that imports configured concrete adapters."""

import importlib
import json
import os
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import Field

from .controller import Controller
from .policy import FixedPolicy
from .schemas import Model
from .storage import Store


class RuntimeSettings(Model):
    backend_factory: str = "qagent.backend.simulator:create_backend"
    backend_options: dict = Field(default_factory=dict)
    policy: Literal["fixed", "deepseek"] = "fixed"
    deepseek: dict = Field(default_factory=dict)
    poll_interval: float = Field(default=0.05, ge=0, le=30)


def load_runtime(path: Path | None) -> RuntimeSettings:
    return RuntimeSettings.model_validate(json.loads(path.read_text(encoding="utf-8"))) if path else RuntimeSettings()


def create_controller(store: Store, settings: RuntimeSettings) -> Controller:
    module, separator, name = settings.backend_factory.partition(":")
    if not separator or not module or not name:
        raise ValueError("backend_factory must be a trusted module:function reference")
    factory = getattr(importlib.import_module(module), name)
    backend = factory(settings.backend_options, store.root)
    if settings.policy == "fixed":
        policy = FixedPolicy()
    else:
        from .policies.deepseek import DeepSeekPolicy, DeepSeekSettings

        config = dict(settings.deepseek)
        config["model"] = config.get("model") or os.environ.get("DEEPSEEK_MODEL", "")
        config.setdefault("base_url", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        policy = DeepSeekPolicy(DeepSeekSettings.model_validate(config))
    return Controller(store, backend, policy)


def provenance(settings: RuntimeSettings | None = None) -> dict[str, str]:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, timeout=5, check=True)
        version = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        version = "unversioned"
    result = {"code_version": version, "analysis_version": "lorentzian_least_squares_v1",
            "controller_version": "0.1.0", "verification_version": "independent_spectrum_v1"}
    if settings is not None:
        result["policy"] = settings.policy
        if settings.policy == "deepseek":
            result["model_id"] = settings.deepseek.get("model") or os.environ.get("DEEPSEEK_MODEL", "")
            result["output_method"] = settings.deepseek.get("output_method", "function_calling")
    return result
