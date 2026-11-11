"""Web search behind a small interface. SEARCH_PROVIDER picks the backend.

    search = get_search()                 # SerpAPI when SEARCH_PROVIDER=serpapi
    results = search.search("PM Vishwakarma eligibility site:gov.in", num=10)

Results are cached on disk forever (cache/search/) because free plans have few
searches a month (SerpAPI free: 250). Delete the folder to refresh.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache" / "search"


class SearchError(RuntimeError):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, num: int = 10) -> list[SearchResult]: ...


class CachedSearch:
    """Base: disk cache + call counter. Subclasses implement _search()."""
    name = "base"

    def __init__(self, cache_dir: Path | None = CACHE_DIR):
        self.cache_dir = cache_dir
        self.live_calls = 0

    def _search(self, query: str, num: int) -> list[SearchResult]:
        raise NotImplementedError

    def search(self, query: str, num: int = 10) -> list[SearchResult]:
        path = None
        if self.cache_dir is not None:
            key = hashlib.sha1(f"{self.name}|{num}|{query}".encode()).hexdigest()
            path = self.cache_dir / f"{key}.json"
            if path.exists():
                return [SearchResult(**r) for r in json.loads(path.read_text(encoding="utf-8"))["results"]]
        self.live_calls += 1
        results = self._search(query, num)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"query": query, "results": [asdict(r) for r in results]},
                                       ensure_ascii=False), encoding="utf-8")
        return results


class SerpAPISearch(CachedSearch):
    name = "serpapi"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None, **kw):
        super().__init__(**kw)
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")
        if not self.api_key:
            raise SearchError("SERPAPI_API_KEY is not set")
        self.client = client or httpx.Client(timeout=30)

    def _search(self, query, num):
        try:
            r = self.client.get("https://serpapi.com/search.json",
                                params={"engine": "google", "q": query, "num": num, "gl": "in",
                                        "hl": "en", "api_key": self.api_key})
        except httpx.HTTPError as e:
            raise SearchError(f"serpapi: {e}") from e
        data = r.json()
        if "hasn't returned any results" in str(data.get("error", "")):
            return []                                   # empty result, not a failure
        if r.status_code != 200 or "error" in data:
            raise SearchError(f"serpapi: {data.get('error', r.status_code)}")
        return [SearchResult(o.get("title", ""), o["link"], o.get("snippet", ""))
                for o in data.get("organic_results", []) if o.get("link")]


class TavilySearch(CachedSearch):
    name = "tavily"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None, **kw):
        super().__init__(**kw)
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise SearchError("TAVILY_API_KEY is not set")
        self.client = client or httpx.Client(timeout=30)

    def _search(self, query, num):
        try:
            r = self.client.post("https://api.tavily.com/search",
                                 json={"api_key": self.api_key, "query": query, "max_results": num})
        except httpx.HTTPError as e:
            raise SearchError(f"tavily: {e}") from e
        if r.status_code != 200:
            raise SearchError(f"tavily: HTTP {r.status_code}")
        return [SearchResult(o.get("title", ""), o["url"], o.get("content", "")[:300])
                for o in r.json().get("results", []) if o.get("url")]


class FakeSearch(CachedSearch):
    """For tests: {query substring: [SearchResult...]}; never cached, never networked."""
    name = "fake"

    def __init__(self, results: dict[str, list[SearchResult]] | None = None):
        super().__init__(cache_dir=None)
        self.results = results or {}
        self.queries: list[str] = []

    def _search(self, query, num):
        self.queries.append(query)
        for key, res in self.results.items():
            if key in query:
                return res[:num]
        return []


class NoSearch:
    """Stand-in when no SEARCH_PROVIDER is configured. Work that needs no search (reading queued
    pages, crawling known sites) still runs; a search attempt raises a clear SearchError, which
    callers already handle as "search unavailable"."""
    name = "none"
    live_calls = 0

    def __init__(self, reason: str = "no SEARCH_PROVIDER configured"):
        self.reason = reason

    def search(self, query: str, num: int = 10):
        raise SearchError(f"web search unavailable: {self.reason}")


PROVIDERS = {"serpapi": SerpAPISearch, "tavily": TavilySearch}


def get_search(provider: str | None = None) -> SearchProvider:
    from llm import config  # noqa: F401  (loads .env)
    provider = (provider or os.getenv("SEARCH_PROVIDER") or "").strip().lower()
    if provider not in PROVIDERS:
        raise SearchError(f"SEARCH_PROVIDER must be one of {sorted(PROVIDERS)}, got {provider!r}")
    return PROVIDERS[provider]()
