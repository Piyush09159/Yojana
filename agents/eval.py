"""Measure each agent at its one job against a hand-labelled set. Live: calls the configured models.

    python -m agents.eval --live                 every agent with a gold set
    python -m agents.eval --live reader          one agent
    python scripts/bench_extract.py <models>     the Extractor (needs pages, so it has its own script)

Bars (the plan's "done when"):
    reader     field accuracy >= 90%, language right >= 90%
    verifier   catches >= 90% of unsupported rules, wrongly drops <= 10% of supported ones
Prints tokens and time per agent so cost and quality are judged together.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

from core.fields import check_value
from core.fields import FieldError

from .reader import READER
from .runner import run_agent
from .verifier import VERIFIER

GOLD = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "agent_eval"
BARS = {"reader": {"fields": 0.9, "language": 0.9}, "verifier": {"catch": 0.9, "false_drop": 0.1}}


def _norm(field: str, value):
    try:
        return check_value(field, value)
    except FieldError:
        return value


def eval_reader(llm=None, out=print, pace: float = 0.0) -> dict:
    """pace: seconds between messages. Free tiers cap tokens per minute; pacing measures the agent's
    accuracy, an unpaced run measures how the fallback chain copes with a burst."""
    gold = yaml.safe_load((GOLD / "reader.yaml").read_text(encoding="utf-8"))
    right = total = lang_right = tokens = failed = 0
    wrong: list[str] = []
    sources: dict[str, int] = {}
    t0 = time.perf_counter()
    for n, case in enumerate(gold):
        if n and pace:
            time.sleep(pace)
        res = run_agent(READER, {"text": case["text"], "asked": None}, llm=llm, use_cache=False)
        tokens += res.tokens
        failed += not res.ok
        sources[res.source] = sources.get(res.source, 0) + 1
        got = res.data.facts if res.ok else {}
        lang_right += bool(res.ok and res.data.language == case["language"])
        for f, want in case["facts"].items():
            total += 1
            if _norm(f, got.get(f)) == _norm(f, want):
                right += 1
            elif res.ok:                                   # a failed call is counted once, below
                wrong.append(f"{case['text'][:38]!r}: {f} = {got.get(f)!r}, expected {want!r} [{res.source}]")
        if not res.ok:
            wrong.append(f"{case['text'][:38]!r}: no answer ({str(res.error)[:70]})")
    n = len(gold)
    score = {"fields": right / total if total else 0, "language": lang_right / n if n else 0,
             "tokens": tokens, "seconds": round(time.perf_counter() - t0, 1), "cases": n,
             "failed_calls": failed, "sources": sources}
    out(f"Reader    fields {right}/{total} ({100 * score['fields']:.0f}%), language {lang_right}/{n}, "
        f"{failed} call(s) with no answer, {tokens} tokens, {score['seconds']}s, answered by {sources}"
        f"  -> {'PASS' if passed('reader', score) else 'below bar'}")
    for line in wrong:
        out(f"    {line}")
    return score


def eval_verifier(llm=None, out=print) -> dict:
    gold = yaml.safe_load((GOLD / "verifier.yaml").read_text(encoding="utf-8"))
    rules = [{"field": g["field"], "op": g["op"], "value": g["value"], "quote": g["quote"]} for g in gold]
    t0 = time.perf_counter()
    res = run_agent(VERIFIER, {"scheme": "evaluation set", "rules": rules}, llm=llm, use_cache=False)
    verdicts = res.data if res.ok else []
    bad = [i for i, g in enumerate(gold) if not g["supported"]]
    good = [i for i, g in enumerate(gold) if g["supported"]]
    caught = sum(1 for i in bad if i < len(verdicts) and not verdicts[i]["supported"])
    dropped = sum(1 for i in good if i < len(verdicts) and not verdicts[i]["supported"])
    score = {"catch": caught / len(bad) if bad else 1, "false_drop": dropped / len(good) if good else 0,
             "tokens": res.tokens, "seconds": round(time.perf_counter() - t0, 1), "degraded": res.degraded,
             "source": res.source}
    out(f"Verifier  caught {caught}/{len(bad)} unsupported, wrongly dropped {dropped}/{len(good)} supported, "
        f"{res.tokens} tokens, {score['seconds']}s via {res.source}"
        f"{' (DEGRADED: ' + str(res.error) + ')' if res.degraded else ''}  -> "
        f"{'PASS' if passed('verifier', score) else 'below bar'}")
    for i, g in enumerate(gold):
        if i < len(verdicts) and verdicts[i]["supported"] != g["supported"]:
            out(f"    {g['field']} {g['op']} {g['value']!r}: said {verdicts[i]['supported']}, expected "
                f"{g['supported']} ({verdicts[i].get('reason', '')[:60]})")
    return score


def passed(agent: str, score: dict) -> bool:
    bar = BARS[agent]
    if agent == "reader":
        return score["fields"] >= bar["fields"] and score["language"] >= bar["language"]
    return score["catch"] >= bar["catch"] and score["false_drop"] <= bar["false_drop"] and not score.get("degraded")


EVALS = {"reader": eval_reader, "verifier": eval_verifier}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="python -m agents.eval")
    ap.add_argument("agents", nargs="*", default=list(EVALS))
    ap.add_argument("--live", action="store_true", help="required: this calls the configured models")
    ap.add_argument("--pace", type=float, default=0.0, help="seconds between Reader messages (free-tier limits)")
    args = ap.parse_args()
    if not args.live:
        print("This calls real models (tokens). Run with --live. Extractor: python scripts/bench_extract.py <models>")
        return 2
    ok = True
    for name in args.agents:
        kwargs = {"pace": args.pace} if name == "reader" else {}
        ok &= passed(name, EVALS[name](**kwargs))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
