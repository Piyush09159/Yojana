"""Claude via the official anthropic SDK."""
from __future__ import annotations

import os

import anthropic

from ..base import LLMError, ProviderBase, RawResult

# Models that accept the server-side refusal fallback ("default" routing).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Current Claude models think adaptively, and thinking tokens count toward
# max_tokens, so leave room for it on top of the caller's output budget.
THINKING_HEADROOM = 8000


class AnthropicProvider(ProviderBase):
    provider = "anthropic"

    def __init__(self, model: str, role: str = "default", client=None,
                 timeout: float | None = None, fast: bool = False):
        super().__init__(model, role)
        # with no ANTHROPIC_API_KEY the SDK falls back to an `ant auth login` profile
        self.client = client or anthropic.Anthropic(
            **({"timeout": timeout, "max_retries": 0 if fast else 1} if timeout else {}))
        self.effort = os.getenv("ANTHROPIC_EFFORT") or None   # low | medium | high | xhigh | max
        self.use_fallbacks = model in FALLBACK_MODELS and not os.getenv("ANTHROPIC_BASE_URL")

    def _call(self, messages, system, json_mode, temperature, max_tokens) -> RawResult:
        # temperature is not sent: current Claude models reject non-default sampling values
        params: dict = {"model": self.model, "max_tokens": max_tokens + THINKING_HEADROOM,
                        "messages": messages}
        if system:
            params["system"] = system
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if self.use_fallbacks:
            resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            resp = self.client.messages.create(**params)

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"{self.name}: request refused (category={category})")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return RawResult(text=text, model=resp.model,
                         usage={"input_tokens": resp.usage.input_tokens,
                                "output_tokens": resp.usage.output_tokens})
