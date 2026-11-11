"""Pick a provider and model per role from environment variables.

    get_llm("extractor")   # EXTRACTOR_PROVIDER / EXTRACTOR_MODEL
    get_llm("agent")       # AGENT_PROVIDER / AGENT_MODEL
    get_llm("judge")       # JUDGE_PROVIDER / JUDGE_MODEL

A role with no provider set uses LLM_PROVIDER (default: anthropic). LLM_MODEL
applies only when the role ends up on that same default provider; otherwise the
provider's default model is used. Explicit provider/model arguments (CLI flags)
win over the environment.

Agent roles (saathi/agents): reader (citizen messages), scout (search planning) and verifier
(second opinion on extracted rules). Each uses its own READER_* / SCOUT_* / VERIFIER_* settings and,
when those are not set, the settings of the role it grew out of (extractor, planner, judge), so
existing .env files keep working. Every role has a timeout (ROLE_TIMEOUTS, or <ROLE>_TIMEOUT in
seconds). Roles a person is waiting on (FAST_ROLES) use the providers' fastest settings and do
not wait on a busy provider; they move to the next one at once.

shared_llm(role) returns one client per role for the whole process, so the fallback chain
remembers which provider is rate-limited instead of finding out again on every request.
"""
from __future__ import annotations

import importlib
import inspect
import os
import threading
import warnings
from pathlib import Path

from dotenv import load_dotenv

from .base import LLMClient, LLMError

ROLES = ("extractor", "agent", "judge", "planner", "reader", "scout", "verifier")
# agent role -> the older role whose settings it falls back to
ROLE_ALIASES = {"reader": "extractor", "scout": "planner", "verifier": "judge"}
# seconds one model call may take before it counts as "busy" (then retried or moved on)
ROLE_TIMEOUTS = {"reader": 15, "agent": 25, "scout": 30, "planner": 30, "extractor": 120,
                 "verifier": 45, "judge": 45}
# a person is waiting: fastest provider settings, no waiting on a busy provider
FAST_ROLES = frozenset({"reader", "agent"})

PROVIDERS = {
    "anthropic": ("anthropic_provider", "AnthropicProvider"),
    "openai": ("openai_provider", "OpenAIProvider"),
    "gemini": ("gemini_provider", "GeminiProvider"),
    "local": ("local_provider", "LocalProvider"),
    "mock": ("mock_provider", "MockProvider"),
}

# Defaults only; set <ROLE>_MODEL or LLM_MODEL to choose. Anthropic default follows blueprint section 6.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-3.8-flash",
    "local": "qwen2.5:7b-instruct",
    "mock": "mock",
}

# Load .env once, at import, without overriding variables already set.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def role_timeout(role: str) -> float:
    raw = os.getenv(f"{role.upper()}_TIMEOUT")
    try:
        return float(raw) if raw else float(ROLE_TIMEOUTS.get(role, 60))
    except ValueError:
        return float(ROLE_TIMEOUTS.get(role, 60))


def _alias_in_use(role: str) -> str | None:
    """The older role whose settings apply, when this agent role has no provider of its own."""
    alias = ROLE_ALIASES.get(role)
    return alias if alias and not os.getenv(f"{role.upper()}_PROVIDER") else None


def resolve(role: str, provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    """Return (provider, model) for a role without building a client."""
    if role not in ROLES:
        raise LLMError(f"unknown LLM role {role!r}; expected one of {ROLES}")
    key = role.upper()
    alias = _alias_in_use(role) if provider is None else None
    default_provider = (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    provider = (provider or os.getenv(f"{key}_PROVIDER")
                or (os.getenv(f"{alias.upper()}_PROVIDER") if alias else None) or default_provider).strip().lower()
    if provider not in PROVIDERS:
        raise LLMError(f"unknown LLM provider {provider!r}; expected one of {sorted(PROVIDERS)}")
    model = model or os.getenv(f"{key}_MODEL") or (os.getenv(f"{alias.upper()}_MODEL") if alias else None)
    if not model and provider == default_provider:
        model = os.getenv("LLM_MODEL")
    return provider, (model or DEFAULT_MODELS[provider]).strip()


def resolve_fallbacks(role: str) -> list[tuple[str, str]]:
    """Ordered (provider, model) pairs to try when the role's provider is out of capacity."""
    key = role.upper()
    alias = _alias_in_use(role)
    chain = (os.getenv(f"{key}_FALLBACKS") or (os.getenv(f"{alias.upper()}_FALLBACKS") if alias else None)
             or os.getenv("LLM_FALLBACKS"))
    pairs: list[tuple[str, str]] = []
    if chain:
        for item in chain.split(","):
            provider, _, model = item.strip().partition(":")     # model names may contain ':' too
            provider = provider.strip().lower()
            if provider:
                pairs.append((provider, model.strip() or DEFAULT_MODELS.get(provider, "")))
    else:
        provider = os.getenv(f"{key}_FALLBACK_PROVIDER") or os.getenv("LLM_FALLBACK_PROVIDER")
        if provider:
            provider = provider.strip().lower()
            model = (os.getenv(f"{key}_FALLBACK_MODEL") or os.getenv("LLM_FALLBACK_MODEL")
                     or DEFAULT_MODELS.get(provider, ""))
            pairs.append((provider, model.strip()))
    for provider, _ in pairs:
        if provider not in PROVIDERS:
            raise LLMError(f"unknown fallback provider {provider!r}")
    return pairs


def get_llm(role: str, *, provider: str | None = None, model: str | None = None,
            fallback: bool = True, **kwargs) -> LLMClient:
    """Build the client for a role. Extra kwargs go to the provider (e.g. responses= for mock).
    If a fallback provider is configured (and no explicit provider was asked for), the client
    switches to it on rate-limit / quota errors."""
    explicit = provider is not None
    provider, model = resolve(role, provider, model)
    if role in ("judge", "verifier") and (provider, model) == resolve("extractor"):
        warnings.warn(f"{role} and extractor both use {provider}:{model}; the {role} would be "
                      f"grading its own work. Set {role.upper()}_PROVIDER/{role.upper()}_MODEL.",
                      UserWarning, stacklevel=2)
    primary = _build(provider, model, role, **kwargs)
    chain = [fb for fb in (resolve_fallbacks(role) if fallback and not explicit else []) if fb != (provider, model)]
    if chain:
        from .fallback import BUSY_TRIES, FallbackClient
        return FallbackClient(primary, *[_build(p, m, role) for p, m in chain],
                              busy_tries=1 if role in FAST_ROLES else BUSY_TRIES)
    return primary


_shared: dict[tuple, LLMClient] = {}
_shared_lock = threading.Lock()


def shared_llm(role: str) -> LLMClient:
    """One client per role (and per configuration) for the whole process. The fallback chain inside
    it remembers which providers are capped or busy, so later calls skip them straight away."""
    key = (role, resolve(role), tuple(resolve_fallbacks(role)))
    with _shared_lock:
        client = _shared.get(key)
        if client is None:
            client = _shared[key] = get_llm(role)
        return client


def reset_shared() -> None:
    """Forget shared clients (tests, or after changing the environment)."""
    with _shared_lock:
        _shared.clear()


def _build(provider: str, model: str, role: str, **kwargs) -> LLMClient:
    module_name, class_name = PROVIDERS[provider]
    try:
        module = importlib.import_module(f".providers.{module_name}", __package__)
    except ImportError as e:
        raise LLMError(f"provider {provider!r} needs its SDK installed: {e}") from e
    cls = getattr(module, class_name)
    accepted = inspect.signature(cls).parameters
    # every provider that supports them gets the role's timeout and speed preference
    if "timeout" in accepted:
        kwargs.setdefault("timeout", role_timeout(role))
    if "fast" in accepted:
        kwargs.setdefault("fast", role in FAST_ROLES)
    try:
        return cls(model, role, **kwargs)
    except LLMError:
        raise
    except Exception as e:  # e.g. missing API key raised by the vendor SDK
        raise LLMError(f"could not set up {provider}:{model}: {e}") from e
