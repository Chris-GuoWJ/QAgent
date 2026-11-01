"""Hardware-only composition root; reading configuration never connects devices."""

import os
from pathlib import Path

from pydantic import Field

from qagent.core.config import read_json_config
from qagent.experiments.parameters import QuantumXSettings
from qagent.core.models import Model
from qagent.core.storage import Store


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = PROJECT_ROOT / "config" / "runtime.json"


class RuntimeSettings(Model):
    quantumx: QuantumXSettings | None = None
    deepseek: dict = Field(default_factory=dict)
    state_dir: Path = PROJECT_ROOT / "data" / "state"
    result_dir: Path = PROJECT_ROOT / "result"
    input_archive_dir: Path = PROJECT_ROOT / "config" / "history"


def load_runtime(path=None):
    path = Path(path or DEFAULT_RUNTIME).resolve()
    if not path.exists():
        raise ValueError("找不到运行配置，请先填写 " + str(path))
    data = read_json_config(path)
    for field in ("state_dir", "result_dir", "input_archive_dir"):
        if field in data:
            data[field] = str((path.parent / data[field]).resolve())
    config = data.get("quantumx", {})
    for field in ("source_dir", "hardware_config", "qubits_config", "driver_dir"):
        if config.get(field) is not None:
            config[field] = str((path.parent / config[field]).resolve())
    config["parameter_files"] = {name: str((path.parent / value).resolve())
                                 for name, value in config.get("parameter_files", {}).items()}
    settings = RuntimeSettings.model_validate(data)
    if settings.quantumx is None:
        raise ValueError("config/runtime.json 必须配置 quantumx 真机实验上下文")
    return settings


def create_store(storage, settings):
    return Store(storage or settings.state_dir, result_root=settings.result_dir, config_root=settings.input_archive_dir)


def create_service(store, settings, *, on_event=None):
    from qagent.core.runner import QuantumXService

    if settings.quantumx is None:
        raise ValueError("缺少 QuantumX 真机配置")
    return QuantumXService(store, settings.quantumx, on_event=on_event)


def deepseek_settings(settings):
    from qagent.agent.model import DeepSeekSettings
    from qagent.agent.credentials import load_local_credentials

    config = dict(settings.deepseek)
    load_local_credentials(extra_keys=(config.get("api_key_env", "DEEPSEEK_API_KEY"),))
    config["model"] = config.get("model") or os.environ.get("DEEPSEEK_MODEL", "")
    if not config["model"]:
        raise ValueError("缺少 DEEPSEEK_MODEL，请在启动目录本地配置模型标识")
    config.setdefault("base_url", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    return DeepSeekSettings.model_validate(config)
