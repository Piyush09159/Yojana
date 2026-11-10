"""OpenAI, and any OpenAI-compatible API (Groq, Together, Ollama, vLLM) when OPENAI_BASE_URL is set."""
from __future__ import annotations

import os

from openai import OpenAI

from ..base import ProviderBase, RawResult

# reasoning models only accept the default temperature
_NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")
# reasoning models spend hidden tokens before answering, and those count toward the limit
_REASONING_PREFIXES = _NO_TEMPERATURE_PREFIXES + ("openai/gpt-oss", "gpt-oss", "qwen/qwen3", "qwen3",
                                               "nemotron", "deepseek-r1")
REASONING_HEADROOM = 4000


class OpenAIProvider(ProviderBase):
    provider = "openai"

    def __init__(self, model: str, role: str = "default", client=None,
                 base_url: str | None = None, api_key: str | None = None):
        super().__init__(model, role)
        base_url = base_url or os.getenv("OPENAI_BASE_URL") or None
        self.compatible = base_url is not None
        if client is None:
            # local servers such as Ollama need no key, but the SDK insists on one
            api_key = api_key or os.getenv("OPENAI_API_KEY") or ("not-needed" if base_url else None)
            client = OpenAI(api_key=api_key, base_url=base_url,
                            timeout=float(os.getenv('LLM_TIMEOUT_SECONDS', '30')), max_retries=0)
        self.client = client

    def _call(self, messages, system, json_mode, temperature, max_tokens) -> RawResult:
        msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
        params: dict = {"model": self.model, "messages": msgs}
        # official API renamed the limit; compatible servers mostly still expect max_tokens
        if self.model.startswith(_REASONING_PREFIXES):
            max_tokens += REASONING_HEADROOM
        params["max_tokens" if self.compatible else "max_completion_tokens"] = max_tokens
        if not self.model.startswith(_NO_TEMPERATURE_PREFIXES):
            params["temperature"] = temperature
        if json_mode:
            params["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**params)
        except Exception as e:
            # Groq rejects replies its own JSON check dislikes (often empty); our parser and
            # schema validation in ProviderBase are stricter anyway, so retry without JSON mode
            if not json_mode or "json_validate_failed" not in str(e):
                raise
            params.pop("response_format")
            resp = self.client.chat.completions.create(**params)
        usage = resp.usage
        return RawResult(text=resp.choices[0].message.content or "", model=resp.model or self.model,
                         usage={"input_tokens": getattr(usage, "prompt_tokens", 0),
                                "output_tokens": getattr(usage, "completion_tokens", 0)})
