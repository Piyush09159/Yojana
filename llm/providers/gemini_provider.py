"""Google Gemini via the google-genai SDK."""
from __future__ import annotations

import os

from google import genai
from google.genai import types

from ..base import ProviderBase, RawResult

# current Gemini models think by default, and thinking tokens count toward max_output_tokens
THINKING_HEADROOM = 4000


class GeminiProvider(ProviderBase):
    provider = "gemini"

    def __init__(self, model: str, role: str = "default", client=None,
                 timeout: float | None = None, fast: bool = False):
        super().__init__(model, role)
        options = types.HttpOptions(timeout=int(timeout * 1000)) if timeout else None   # milliseconds
        self.client = client or genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options=options)
        # a person is waiting: think briefly (a full think took 78 s on one message)
        self.fast = fast

    def _call(self, messages, system, json_mode, temperature, max_tokens) -> RawResult:
        contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                                  parts=[types.Part(text=m["content"])])
                    for m in messages]
        settings = dict(system_instruction=system, temperature=temperature,
                        max_output_tokens=max_tokens + THINKING_HEADROOM,
                        response_mime_type="application/json" if json_mode else None)
        if self.fast:
            settings["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        try:
            resp = self.client.models.generate_content(model=self.model, contents=contents,
                                                       config=types.GenerateContentConfig(**settings))
        except Exception as e:
            # a model without thinking levels rejects the setting: ask again without it
            if not self.fast or "think" not in str(e).lower():
                raise
            settings.pop("thinking_config")
            resp = self.client.models.generate_content(model=self.model, contents=contents,
                                                       config=types.GenerateContentConfig(**settings))
        um = resp.usage_metadata
        return RawResult(text=resp.text or "", model=self.model,
                         usage={"input_tokens": getattr(um, "prompt_token_count", 0) or 0,
                                "output_tokens": getattr(um, "candidates_token_count", 0) or 0})
