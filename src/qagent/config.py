"""JSON task/runtime configuration; credentials use a separate startup boundary."""

import json
from pathlib import Path


def read_json_config(path: Path) -> dict:
    resolved = Path(path).resolve()
    for name in (Path(path).name, resolved.name):
        name = name.casefold()
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            raise ValueError("禁止读取 .env 作为任务或运行 JSON 配置；模型凭证由启动器单独加载")
    return json.loads(resolved.read_text(encoding="utf-8"))
