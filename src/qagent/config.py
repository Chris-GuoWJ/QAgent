"""Explicit JSON configuration only. Environment files are never opened."""

import json
from pathlib import Path


def read_json_config(path: Path) -> dict:
    resolved = Path(path).resolve()
    for name in (Path(path).name, resolved.name):
        name = name.casefold()
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            raise ValueError("禁止读取 .env 配置文件；请使用 JSON 配置或已设置的进程环境变量")
    return json.loads(resolved.read_text(encoding="utf-8"))
