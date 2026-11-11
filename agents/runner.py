"""The one place agents run. run_agent() never raises: every failure becomes an AgentResult the
caller can plan around, so a slow, capped, broken or confused model can never end a conversation
or crash a worker.

    result = run_agent(READER, {"text": "...", "asked": None})
    if result.ok: ...result.data...          # result.degraded: a code fallback answered instead
    else: ...result.error...                 # nothing usable; the caller decides what to do

Steps for every call:
  1. cache     identical input seen before (key is a hash, never the text) -> stored output, 0 tokens
  2. budget    lane's daily token cap reached -> skip the model, use the agent's fallback
  3. model     the role's shared client (llm/config.shared_llm): per-role timeout, fallback chain
               whose busy/capped memory persists across calls
  4. validate  the agent's code check; invalid output counts as a failure
  5. fallback  every model failed -> the agent's deterministic degrade(), marked degraded=True
  6. record    tokens spent per lane and provider (store/runtime.budget_add); cache the output
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from llm import LLMError, shared_llm

log = logging.getLogger("agents")

LANES = ("realtime", "gap", "batch")
# Daily token caps per lane for paid/cloud providers (0 = no cap). Local models are never capped.
DEFAULT_TOKEN_CAPS = {"realtime": 0, "gap": 300_000, "batch": 1_000_000}


class AgentInvalid(ValueError):
    """The agent answered, but its output failed the code check."""


@dataclass
class AgentSpec:
    name: str                                         # reader | replier | scout | extractor | verifier
    role: str                                         # llm/config role that picks the model
    lane: str                                         # default lane: realtime | gap | batch
    call: Callable[[Any, Any], Any]                   # (llm, input) -> output; may raise LLMError
    validate: Callable[[Any, Any], Any] | None = None # (output, input) -> output, or raise AgentInvalid
    degrade: Callable[[Any], Any] | None = None       # input -> output from code alone, or None
    cache_key: Callable[[Any], str | None] | None = None
    cache_ttl: float | None = None                    # seconds; None = forever
    dump: Callable[[Any], Any] = lambda x: x          # output -> JSON for the cache
    load: Callable[[Any], Any] = lambda x: x          # JSON -> output
    est_tokens: int = 2000                            # for the budget check before a call


@dataclass
class AgentResult:
    ok: bool
    data: Any = None
    degraded: bool = False                            # answered by code, not by a model
    source: str = ""                                  # provider:model, "cache" or "fallback"
    error: str | None = None
    tokens: int = 0
    ms: int = 0
    notes: list[str] = field(default_factory=list)


class _Metered:
    """Wraps a client to count the tokens of every call made through it (agents reuse existing
    functions such as understand() and extract_scheme(), which do not return usage)."""

    def __init__(self, client):
        self.client = client
        self.tokens = 0
        self.calls = 0

    def complete(self, messages, **kwargs):
        resp = self.client.complete(messages, **kwargs)
        self.calls += 1
        self.tokens += int(resp.usage.get("input_tokens", 0)) + int(resp.usage.get("output_tokens", 0))
        return resp

    def __getattr__(self, name):
        return getattr(self.client, name)


def token_cap(lane: str) -> int:
    raw = os.getenv(f"BUDGET_TOKENS_{lane.upper()}")
    try:
        return int(raw) if raw not in (None, "") else DEFAULT_TOKEN_CAPS.get(lane, 0)
    except ValueError:
        return DEFAULT_TOKEN_CAPS.get(lane, 0)


def _budget_allows(lane: str, est: int) -> bool:
    cap = token_cap(lane)
    if cap <= 0:
        return True
    try:
        from store import runtime
        return runtime.budget_used(lane)["tokens"] + est <= cap
    except Exception:                         # budget store down: do not block the agent
        return True


def _record(lane: str, provider: str, calls: int, tokens: int) -> None:
    try:
        from store import runtime
        runtime.budget_add(lane, provider, calls=calls, tokens=tokens)
    except Exception as e:
        log.warning("budget not recorded: %s", e)


def _cache(spec: AgentSpec, key: str | None, value=None, read: bool = True):
    if not key:
        return None
    try:
        from store import runtime
        if read:
            hit = runtime.cache_get(spec.name, key, spec.cache_ttl)
            return None if hit is None else spec.load(hit)
        runtime.cache_put(spec.name, key, spec.dump(value))
    except Exception as e:                    # the cache is an optimisation, never a dependency
        log.warning("cache %s for %s: %s", "read" if read else "write", spec.name, e)
    return None


def _fallback(spec: AgentSpec, inp, error: str, t0: float, tokens: int = 0) -> AgentResult:
    ms = int((time.perf_counter() - t0) * 1000)
    if spec.degrade is not None:
        try:
            data = spec.degrade(inp)
            if data is not None:
                return AgentResult(True, data, degraded=True, source="fallback", error=error, tokens=tokens, ms=ms)
        except Exception as e:
            error = f"{error}; fallback failed: {type(e).__name__}: {e}"
    return AgentResult(False, None, degraded=True, source="none", error=error, tokens=tokens, ms=ms)


def run_agent(spec: AgentSpec, inp, *, llm=None, lane: str | None = None, use_cache: bool = True) -> AgentResult:
    """Run one agent on one input. Never raises. use_cache=False always asks the model (evaluations)."""
    t0 = time.perf_counter()
    lane = lane or spec.lane
    try:
        key = spec.cache_key(inp) if (spec.cache_key and use_cache) else None
    except Exception:
        key = None
    cached = _cache(spec, key)
    if cached is not None:
        return AgentResult(True, cached, source="cache", ms=int((time.perf_counter() - t0) * 1000))

    if not _budget_allows(lane, spec.est_tokens):
        return _fallback(spec, inp, f"daily token budget for the {lane} lane is used up", t0)
    try:
        client = _Metered(llm if llm is not None else shared_llm(spec.role))
    except LLMError as e:
        return _fallback(spec, inp, f"no model for {spec.role}: {e}", t0)

    try:
        output = spec.call(client, inp)
        if spec.validate is not None:
            output = spec.validate(output, inp)
    except AgentInvalid as e:
        _record(lane, getattr(client, "name", "?"), client.calls, client.tokens)
        return _fallback(spec, inp, f"invalid output: {e}", t0, client.tokens)
    except LLMError as e:
        _record(lane, getattr(client, "name", "?"), client.calls, client.tokens)
        return _fallback(spec, inp, str(e)[:300], t0, client.tokens)
    except Exception as e:                    # a bug must not take the conversation down with it
        log.exception("agent %s crashed", spec.name)
        return _fallback(spec, inp, f"{type(e).__name__}: {e}"[:300], t0, client.tokens)

    provider = getattr(client, "name", "?")
    _record(lane, provider, client.calls, client.tokens)
    _cache(spec, key, output, read=False)
    return AgentResult(True, output, source=provider, tokens=client.tokens,
                       ms=int((time.perf_counter() - t0) * 1000))
