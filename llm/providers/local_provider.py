"""Local models through an OpenAI-compatible server (Ollama, LM Studio, vLLM). No network, no quota.

LOCAL_BASE_URL defaults to Ollama: http://localhost:11434/v1. Kept separate from OPENAI_BASE_URL
so a local model can run next to Groq.
"""
from __future__ import annotations

import os

from .openai_provider import OpenAIProvider


class LocalProvider(OpenAIProvider):
    provider = "local"

    # seconds added to the role's timeout: the first call after a pause loads the model into memory
    LOAD_TIME = 15

    def __init__(self, model: str, role: str = "default", client=None,
                 timeout: float | None = None, fast: bool = False):
        super().__init__(model, role, client=client,
                         base_url=os.getenv("LOCAL_BASE_URL") or "http://localhost:11434/v1",
                         api_key=os.getenv("LOCAL_API_KEY") or "ollama",
                         timeout=timeout + self.LOAD_TIME if timeout else None, fast=fast)
