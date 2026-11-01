"""Conversation model only; all experiment calculations run in QuantumX."""

import os

from pydantic import Field

from ..schemas import Model


class DeepSeekSettings(Model):
    model: str = Field(min_length=1)
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    thinking: str | None = None


def create_chat_model(settings):
    from langchain_deepseek import ChatDeepSeek

    key = os.environ.get(settings.api_key_env)
    if not key:
        raise ValueError(f"缺少 {settings.api_key_env}，请在启动目录本地配置凭证")
    params = dict(model=settings.model, api_key=key, api_base=settings.base_url,
                  timeout=settings.timeout_seconds, max_retries=0)
    if settings.thinking:
        params["extra_body"] = {"thinking": {"type": settings.thinking}}
    return ChatDeepSeek(**params)
