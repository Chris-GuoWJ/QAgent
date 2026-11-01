"""Application-only credential bootstrap; never expose file contents to agents."""

import os
from pathlib import Path


MODEL_VARIABLES = ("DEEPSEEK_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")


def load_local_credentials(directory=None, *, extra_keys=(), environ=None, loader=None):
    """Import only model settings from cwd/.env, preserving nonempty process values.

    Development tools/tests must disable real file loading with
    QAGENT_NO_ENV_FILE=1. Tests may inject a loader and an isolated mapping.
    Explicit paths prevent python-dotenv from searching ancestor directories.
    """
    target = os.environ if environ is None else environ
    if target.get("QAGENT_NO_ENV_FILE", "").casefold() in {"1", "true", "yes"}:
        return
    if loader is None:
        try:
            from dotenv import dotenv_values
        except ImportError:
            raise ValueError('请安装凭证加载依赖：python -m pip install "python-dotenv>=1,<2"') from None
        loader = dotenv_values
    path = (Path.cwd() if directory is None else Path(directory)) / ".env"
    try:
        values = loader(dotenv_path=path, encoding="utf-8-sig", interpolate=False, verbose=False)
        allowed = set(MODEL_VARIABLES) | set(extra_keys)
        for name in allowed:
            value = values.get(name)
            if not target.get(name) and isinstance(value, str) and value.strip():
                target[name] = value.strip()
    except Exception as exc:
        # Do not echo parser input, exception bodies, or credential values.
        raise ValueError("本地模型凭证加载失败（" + type(exc).__name__ + "）；请在本机检查文件编码或改用进程环境变量") from None
