"""Tokens and cost of the LLM calls made since a point in time, from logs/llm_calls.jsonl.

    python scripts/query_cost.py --since 2026-11-11T07:30:00
    python scripts/query_cost.py --since 2026-11-11T07:30:00 --price gemini-3.8-flash=0.30,2.50

--price model=input,output is US dollars per million tokens. Prices change: take them from the
provider's pricing page. Without --price only token counts are shown. On free tiers the money cost
is zero; the tokens show how much of the daily free quota one query uses.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "llm_calls.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="ISO time (UTC), e.g. 2026-11-11T07:30:00")
    ap.add_argument("--price", action="append", default=[], help="model=input_usd_per_M,output_usd_per_M")
    args = ap.parse_args()
    prices = {}
    for p in args.price:
        model, _, pair = p.partition("=")
        i, _, o = pair.partition(",")
        prices[model] = (float(i), float(o))
    rows = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r["ts"][:19] >= args.since[:19]]
    by = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "roles": set(), "errors": 0, "ms": 0})
    for r in rows:
        b = by[(r["provider"], r["model"])]
        b["calls"] += 1
        b["in"] += r["usage"].get("input_tokens", 0)
        b["out"] += r["usage"].get("output_tokens", 0)
        b["roles"].add(r["role"])
        b["errors"] += not r["ok"]
        b["ms"] += r.get("latency_ms", 0)
    total_cost, total_in, total_out = 0.0, 0, 0
    print(f"{len(rows)} LLM calls since {args.since}")
    for (provider, model), b in sorted(by.items()):
        cost = ""
        if model in prices:
            c = b["in"] / 1e6 * prices[model][0] + b["out"] / 1e6 * prices[model][1]
            total_cost += c
            cost = f"  ${c:.5f}"
        total_in += b["in"]
        total_out += b["out"]
        print(f"  {provider}:{model:28} {b['calls']:3} calls  in {b['in']:7,}  out {b['out']:6,} tokens"
              f"  {b['ms'] / 1000:6.1f}s  roles {','.join(sorted(b['roles']))}"
              + (f"  errors {b['errors']}" if b["errors"] else "") + cost)
    print(f"  total: in {total_in:,}  out {total_out:,} tokens"
          + (f"  = ${total_cost:.5f} at the given prices" if prices else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
