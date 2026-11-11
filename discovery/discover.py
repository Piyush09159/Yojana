"""Find schemes on official websites and add them to schemes/discovered/.

    d = Discoverer()                              # real LLM, search and fetcher from .env
    report = d.discover_for_profile(profile)      # per-user: brainstorm names -> search -> fetch -> extract
    report = d.ingest_queries(["..."])            # bulk: used by `python -m ingest`

Every URL that was processed is remembered in the database (crawl_urls) so it is not fetched
or extracted twice. Search results are cached by discovery/search.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.fields import load_fields
from ingest.extract import extract_scheme
from ingest.fetch import FetchError, Fetcher, is_blocked, is_official, load_policy
from ingest.source_map import meta_for_url
from llm import LLMClient, LLMError, get_llm

from store import db

from .catalog import load_catalog, norm_name, save_discovered
from .search import NoSearch, SearchError, SearchProvider, get_search

ROOT = Path(__file__).resolve().parent.parent

BRAINSTORM_SYSTEM = (
    "You know Indian central and state government welfare schemes. Given a person's profile, list "
    "official scheme names they may qualify for: central schemes and schemes of their state. "
    "Only real, currently running government schemes; use the official English name. "
    "You are only suggesting names to look up; you do not decide eligibility.")
BRAINSTORM_SCHEMA = {
    "type": "object",
    "properties": {"schemes": {"type": "array", "items": {"type": "string"}, "maxItems": 15}},
    "required": ["schemes"],
}


@dataclass
class DiscoveryReport:
    searches: int = 0
    pages_fetched: int = 0
    new_schemes: list[str] = field(default_factory=list)
    updated_schemes: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    mentioned: list[str] = field(default_factory=list)
    rules_flagged: int = 0                     # rules whose quote did not mention their field
    rules_dropped: int = 0                     # of those, moved to "check yourself" by the Verifier
    not_for_people: int = 0                    # village / institution programmes not saved


class Discoverer:
    def __init__(self, llm: LLMClient | None = None, search: SearchProvider | None = None,
                 fetcher: Fetcher | None = None, db_path: Path | None = None,
                 log: Callable[[str], None] = lambda m: None, verifier: LLMClient | None = None,
                 lane: str = "batch"):
        from llm import shared_llm
        self.llm = llm or shared_llm("extractor")
        self.verifier = verifier                # None: the verifier role's shared client, when needed
        self.lane = lane                        # budget lane the agents spend from: gap | batch
        if search is None:
            try:
                search = get_search()
            except SearchError as e:       # no key: page reading still works, searches say why not
                search = NoSearch(str(e))
        self.search = search
        self.fetcher = fetcher or Fetcher()
        self.db_path = db_path
        self.log = log

    # ---------- bookkeeping: every page we have already looked at ----------
    def _remember(self, url: str, status: str, scheme_id: str | None = None, note: str = "") -> None:
        db.mark_url(url, status, scheme_id, note, self.db_path)

    def _seen(self, url: str) -> dict | None:
        """A URL we already finished with. "queued" means found but not read yet, so not seen."""
        rec = db.url_record(url, self.db_path)
        return rec if rec and rec["status"] != "queued" else None

    # ---------- query building ----------
    def brainstorm_names(self, profile: dict) -> list[str]:
        facts = {k: v for k, v in profile.items() if not k.startswith("_") and v is not None}
        try:
            r = self.llm.complete([{"role": "user", "content": "Profile: " + json.dumps(facts)}],
                                  system=BRAINSTORM_SYSTEM, json_schema=BRAINSTORM_SCHEMA, max_tokens=800)
        except LLMError as e:
            self.log(f"  (could not brainstorm scheme names: {e})")
            return []
        return [n.strip() for n in r.data["schemes"] if n.strip()]

    @staticmethod
    def profile_queries(profile: dict) -> list[str]:
        suffix = load_policy()["search_suffix"]
        state = profile.get("state")
        where = state.replace("_", " ").title() + " government" if state else "Government of India"
        who = []
        if profile.get("occupation"):
            who.append(profile["occupation"].replace("_", " "))
        if profile.get("gender") == "female":
            who.append("women")
        if (profile.get("age") or 0) >= 60:
            who.append("senior citizen")
        if profile.get("is_student"):
            who.append("student scholarship")
        if profile.get("disability_percent"):
            who.append("disabled persons")
        if profile.get("social_category") in ("sc", "st", "obc"):
            who.append(profile["social_category"].upper())
        return [f"{where} scheme for {' '.join(who[:3]) or 'poor families'} eligibility {suffix}"]

    def name_queries(self, names: list[str]) -> list[str]:
        suffix = load_policy()["search_suffix"]
        known = {norm_name(s["name"]) for s in load_catalog(path=self.db_path).values()}
        return [f'"{n}" eligibility {suffix}' for n in names if norm_name(n) not in known]

    # ---------- pipeline ----------
    def process_url(self, url: str, report: DiscoveryReport, extra: dict | None = None) -> str | None:
        """Fetch, extract and save one page. `extra` (agency_type, agency_name, district) is stored
        on the scheme; by default it comes from the state source map."""
        seen = self._seen(url)
        if seen is not None:
            return seen.get("scheme_id")
        try:
            page = self.fetcher.fetch(url)
        except FetchError as e:
            report.skipped.append((url, str(e)))
            self._remember(url, "fetch_error", note=str(e))
            return None
        report.pages_fetched += 1
        from agents.extractor import EXTRACTOR, benefits_a_person
        from agents.runner import run_agent
        from agents.verifier import review
        out = run_agent(EXTRACTOR, page, llm=self.llm, lane=self.lane)
        if not out.ok:
            report.skipped.append((url, f"extraction failed: {out.error}"))
            return None                                        # not remembered: retry next time
        res = out.data
        report.mentioned.extend(res.mentioned_schemes)
        if res.scheme is None:
            report.skipped.append((url, res.error or "no scheme"))
            self._remember(url, "not_scheme", note=res.error or "")
            return None
        if not benefits_a_person(res.scheme):
            report.not_for_people += 1
            report.skipped.append((url, "benefits a village or institution, not a person"))
            self._remember(url, "not_scheme", note="benefits a village or institution, not a person")
            return None
        checked = review(res.scheme, llm=self.verifier, lane=self.lane)
        report.rules_flagged += checked["flagged"]
        report.rules_dropped += checked["dropped"]
        meta = dict(extra if extra is not None else meta_for_url(page.final_url))
        if meta.get("district"):
            # A Zilla Parishad site carries both its own schemes and state/central ones. Only the
            # extractor can tell them apart, so a state-wide scheme keeps no district and stays
            # visible to the whole state.
            if res.scheme.get("district_specific"):
                res.scheme.setdefault("state", "maharashtra")
            else:
                meta.pop("district")
        res.scheme.update({k: v for k, v in meta.items() if v})
        sid, status = save_discovered(res.scheme, self.db_path)
        self._remember(url, "scheme", sid)
        if status in ("new", "updated"):
            (report.new_schemes if status == "new" else report.updated_schemes).append(sid)
            self.log(f"  + {res.scheme['name']} ({res.scheme['source_site']}, "
                     f"{res.kept_rules} rules, {len(res.scheme['unmapped_criteria'])} to check manually)")
        return sid

    def ingest_queries(self, queries: list[str], max_searches: int = 10, max_pages: int = 20,
                       per_query: int = 3, report: DiscoveryReport | None = None) -> DiscoveryReport:
        """max_searches counts paid (uncached) searches made in this call; cached ones are free."""
        report = report or DiscoveryReport()
        seen: set[str] = set()
        credits_at_start = self.search.live_calls
        for q in queries:
            if self.search.live_calls - credits_at_start >= max_searches or report.pages_fetched >= max_pages:
                break
            try:
                results = self.search.search(q, num=10)
            except SearchError as e:
                self.log(f"  (search failed: {e})")
                break
            report.searches += 1
            found, tried = 0, 0
            for r in results:
                # keep going down the results until `per_query` schemes are found (max 4 tries)
                if found >= per_query or tried >= 4 or report.pages_fetched >= max_pages:
                    break
                if r.url in seen or is_blocked(r.url) or not is_official(r.url):
                    continue
                seen.add(r.url)
                tried += 1
                if self.process_url(r.url, report):
                    found += 1
        return report

    def discover_for_profile(self, profile: dict, max_searches: int = 6, max_pages: int = 10) -> DiscoveryReport:
        names = self.brainstorm_names(profile)
        if names:
            self.log("  looking up: " + ", ".join(names[:8]) + ("..." if len(names) > 8 else ""))
        queries = self.name_queries(names) + self.profile_queries(profile)
        return self.ingest_queries(queries, max_searches=max_searches, max_pages=max_pages, per_query=1)


def state_values() -> list[str]:
    return list(load_fields()["state"]["values"])
