"""Append one JSON line per LLM call to logs/llm_calls.jsonl.

Only metadata is written (provider, model, role, tokens, latency, error).
Prompt and reply text are never logged, so no PII reaches the file.
Set LLM_LOG_PATH to change the file, or LLM_LOG=0 to turn logging off.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "llm_calls.jsonl"


def log_call(**record) -> None:
    if os.getenv("LLM_LOG", "1") == "0":
        return
    if record.get('error'):
        # SDK/validation exceptions can echo prompts, account IDs or credentials.
        record['error'] = 'provider_or_validation_error'
    path = Path(os.getenv("LLM_LOG_PATH") or DEFAULT_PATH)
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break a call
