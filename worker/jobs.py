"""What each background job does. A job is one hand-off between agents; together they form the
gap lane (and the batch lane reuses `extract`):

    gapfill(segment, profile)   Scout -> searches (official sites, daily budget) -> extract job per page
    extract(url)                Extractor -> quote check -> person check -> Verifier -> save
    notify(segment)             engine re-ranks for each conversation in the segment -> follow-up

Every handler is idempotent (keys: segment, url, segment) and returns a small summary dict. A
handler raises RetryLater when the work should be tried again (a model or site was unavailable);
any other outcome, including "not a scheme page", is final.
"""
from __future__ import annotations

import os

from agents.runner import run_agent
from agents.scout import SCOUT
from ingest.fetch import is_blocked, is_official, load_policy
from saathi import reply
from saathi.senders import deliver
from saathi.shortlist import build_shortlist
from saathi.sufficiency import TARGET, pick
from store import db, runtime

MAX_SEARCHES_PER_GAP = 2
MAX_PAGES_PER_GAP = 4
# Results per search. A search costs one credit whatever the count, and Google through SerpAPI does
# not honour site:/as_sitesearch domain filters (measured 2026-11-11: 0-2 official results in 10), so
# more results per paid search means more official pages for the same budget; code keeps only official.
RESULTS_PER_SEARCH = 30
DEFAULT_SEARCHES_PER_DAY = 8            # SerpAPI's free plan is 250 a month
NOTIFY_DELAY = 20                       # seconds: one follow-up after several pages, not one per page


class RetryLater(RuntimeError):
    """Temporary failure: put the job back and try again later."""


def searches_left_today() -> int:
    try:
        cap = int(os.getenv("BUDGET_SEARCHES_DAY") or DEFAULT_SEARCHES_PER_DAY)
    except ValueError:
        cap = DEFAULT_SEARCHES_PER_DAY
    return max(0, cap - runtime.budget_used()["searches"])


def _discoverer(lane: str):
    from discovery.discover import Discoverer
    return Discoverer(lane=lane)


# ---------- gapfill ----------

def gapfill(payload: dict, discoverer=None) -> dict:
    profile, seg = payload["profile"], payload["segment"]
    scout = run_agent(SCOUT, profile)
    plan = scout.data or {"needs": [], "scheme_names": []}
    d = discoverer or _discoverer("gap")
    if d.search.name == "none":
        return {"scout": scout.source, "searches": 0, "pages_queued": 0, "note": "no SEARCH_PROVIDER configured"}

    suffix = load_policy()["search_suffix"]
    where = (profile.get("state") or "india").replace("_", " ")
    queries = d.name_queries(plan["scheme_names"])
    queries += [f"{t} {where} eligibility {suffix}" for n in plan["needs"] for t in n["search_terms"][:1]]
    allowed = min(MAX_SEARCHES_PER_GAP, searches_left_today())

    urls: list[str] = []
    searched = 0
    for q in queries:
        if searched >= allowed or len(urls) >= MAX_PAGES_PER_GAP:
            break
        before = d.search.live_calls
        try:
            results = d.search.search(q, num=RESULTS_PER_SEARCH)
        except Exception as e:                                  # search down: the gap stays logged
            return {"scout": scout.source, "searches": searched, "pages_queued": len(urls), "note": f"search: {e}"[:120]}
        paid = d.search.live_calls - before
        if paid:
            searched += paid
            runtime.budget_add("gap", f"search:{d.search.name}", searches=paid)
        for r in results:
            if len(urls) >= MAX_PAGES_PER_GAP:
                break
            if r.url in urls or is_blocked(r.url) or not is_official(r.url):
                continue
            rec = db.url_record(r.url)
            if rec and rec["status"] != "queued":
                continue                                        # already read
            urls.append(r.url)

    queued = sum(1 for u in urls if runtime.job_enqueue("extract", {"url": u, "segment": seg, "lane": "gap"},
                                                        dedupe_key=f"extract:{u}") is not None)
    runtime.segment_mark(seg, found=len(urls))
    return {"scout": scout.source, "scout_degraded": scout.degraded, "searches": searched, "pages_queued": queued,
            "budget_left": searches_left_today()}


# ---------- extract ----------

def extract(payload: dict, discoverer=None) -> dict:
    from discovery.discover import DiscoveryReport
    url, lane = payload["url"], payload.get("lane", "batch")
    d = discoverer or _discoverer(lane)
    report = DiscoveryReport()
    sid = d.process_url(url, report, extra=payload.get("meta"))
    temporary = [why for _, why in report.skipped if why.startswith("extraction failed")]
    if sid is None and temporary:
        raise RetryLater(temporary[0][:200])
    changed = sid is not None and (sid in report.new_schemes or sid in report.updated_schemes)
    if changed and payload.get("segment"):
        delay = float(os.getenv("NOTIFY_DELAY") or NOTIFY_DELAY)
        runtime.job_enqueue("notify", {"segment": payload["segment"]}, dedupe_key=f"notify:{payload['segment']}",
                            delay=delay)
    return {"scheme": sid, "new": sid in report.new_schemes if sid else False,
            "flagged": report.rules_flagged, "dropped": report.rules_dropped,
            "not_for_people": report.not_for_people,
            "skipped": report.skipped[0][1][:80] if report.skipped else None}


# ---------- notify ----------

def notify(payload: dict, orchestrator=None) -> dict:
    from saathi.orchestrator import Orchestrator
    o = orchestrator or Orchestrator(enqueue_gaps=False)
    told = sent = 0
    for chat_ref, state in runtime.conversations_in_segment(payload["segment"]):
        profile = state.get("profile") or {}
        catalog = o.catalog(profile)
        cards, _ = pick(build_shortlist({**profile, "_asked": []}, catalog, n_others=15), catalog, TARGET)
        shown = set(state.get("shown") or [])
        new = [c for c in cards if c.get("good") and c["id"] not in shown]
        if not new:
            continue
        text = reply.followup_text(new, state.get("lang", "en"))
        delivered = deliver(state, text)
        state.setdefault("followups", []).append({"text": text, "delivered": delivered, "at": runtime.now()})
        state["shown"] = list(shown | {c["id"] for c in new})
        runtime.conversation_save(chat_ref, state)
        told += 1
        sent += delivered
    return {"conversations_told": told, "sent_now": sent}


HANDLERS = {"gapfill": gapfill, "extract": extract, "notify": notify}
