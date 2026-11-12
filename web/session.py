"""One web user's session: form profile -> investigation -> reasoned questions -> results, as events.

Events (kind, data) are appended to `events` and streamed to the browser by web/app.py:
  thinking  {summary, needs | uncovered}     the planner's reasoning, in short
  action    {kind, target, why}              a crawl or search being run
  found     {id, name, site, awareness, status}
  notice / progress {text}
  results   build_shortlist(...) JSON         (sent several times as it improves)
  question  {field, question, reason, type, choices, schemes}
  reply     {text}                            answer to a free question
  done      {}
Nothing here decides eligibility: core/ does, through build_shortlist.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

from core.fields import FieldError, check_value
from llm import LLMClient, LLMError
from saathi import explain
from saathi.investigator import Budget, Investigator
from saathi.questioner import next_question
from saathi.shortlist import build_shortlist, find_scheme, scheme_details
from saathi.understand import understand

# one investigation at a time: they share the page cache, search budget and discovery index
INVESTIGATION_LOCK = threading.Lock()


class WebSession:
    def __init__(self, profile: dict, lang: str, *, planner: LLMClient | None, extractor: LLMClient | None,
                 agent: LLMClient | None, discoverer=None, catalog_loader: Callable[[], dict],
                 budget: Budget | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.profile = {**profile, "_asked": []}
        self.lang = lang if lang in ("en", "hi", "mr") else "en"
        self.planner, self.extractor, self.agent = planner, extractor, agent
        self.discoverer = discoverer
        self.catalog_loader = catalog_loader
        self.budget = budget or Budget()
        self.catalog = catalog_loader()
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.lock = threading.Lock()
        self.pending: dict | None = None
        self.situation = ""
        self.shortlist: dict | None = None
        self.state = "starting"            # investigating | asking | done
        self.created = time.time()
        self.on_change = None
        self.next_seq = 0

    # ---------- events ----------
    def emit(self, kind: str, data: dict | None = None) -> None:
        with self.cond:
            self.events.append({"seq": self.next_seq, "kind": kind, "data": data or {}})
            self.next_seq += 1
            if self.on_change:
                self.events = self.events[-200:]
                self.on_change(self)
            self.cond.notify_all()

    def wait_events(self, after: int, timeout: float = 15.0) -> list[dict]:
        with self.cond:
            if not self.events or self.events[-1]['seq'] < after:
                self.cond.wait(timeout)
            return [event for event in self.events if event['seq'] >= after]

    # ---------- flow ----------
    def start(self) -> None:
        threading.Thread(target=self._guard(self._investigate), daemon=True).start()

    def _guard(self, fn):
        def run(*a):
            try:
                fn(*a)
            except Exception as e:  # keep the page alive and tell the user
                self.emit("notice", {"text": 'Investigation unavailable. Check service configuration and retry.'})
                self.emit("done")
        return run

    def _publish_results(self) -> None:
        self.shortlist = build_shortlist(self.profile, self.catalog)
        self.emit("results", self.shortlist)

    def _investigate(self) -> None:
        with self.lock:
            self.state = "investigating"
            self._publish_results()                         # instant answer from the saved catalog
            with INVESTIGATION_LOCK:
                inv = Investigator(self.planner, self.discoverer, self.budget, emit=self.emit,
                                   catalog_loader=self.catalog_loader).run(self.profile)
            self.situation = inv.analysis.get("summary", "")
            self.catalog = self.catalog_loader()
            self._publish_results()
            self._ask_next()

    def _ask_next(self) -> None:
        q = next_question(self.planner, self.lang, self.profile, self.catalog, self.situation)
        if q is None:
            self.pending = None
            self.state = "done"
            self.emit("done")
            return
        self.pending = q
        self.profile["_asked"].append(q["qid"])
        self.state = "asking"
        self.emit("question", q)

    # ---------- user input ----------
    def answer(self, field: str | None = None, value=None, text: str | None = None, skip: bool = False) -> dict:
        """Record an answer. Structured value (from buttons) is checked by code; free text goes through
        the extractor LLM and is validated against fields.yaml. Returns what was stored."""
        with self.lock:
            stored: dict = {}
            if not skip:
                if field is not None and value is not None:
                    try:
                        stored[field] = check_value(field, value)
                    except FieldError as e:
                        return {"error": str(e)}
                elif text:
                    try:
                        u = understand(self.extractor, text, self.pending)
                        stored = u.facts
                    except LLMError as e:
                        return {"error": 'Language service unavailable. Retry without sharing sensitive information.'}
            self.profile.update(stored)
            self.pending = None
        self.emit("answer_recorded", {"facts": stored})
        threading.Thread(target=self._guard(self._after_answer), daemon=True).start()
        return {"stored": stored}

    def _after_answer(self) -> None:
        with self.lock:
            self._publish_results()
            self._ask_next()

    def ask(self, text: str) -> None:
        threading.Thread(target=self._guard(self._reply), args=(text,), daemon=True).start()

    def _reply(self, text: str) -> None:
        context: dict = {"profile": self.public_profile(), "shortlist": self.shortlist}
        sid = find_scheme(text, self.catalog)
        if sid is None and self.shortlist:
            for c in [self.shortlist.get("start_with")] + self.shortlist.get("others", []) + \
                     self.shortlist.get("lesser_known", []):
                if c and c["name"].lower()[:12] in text.lower():
                    sid = c["id"]
                    break
        if sid:
            context["scheme_details"] = scheme_details(sid, self.profile, self.catalog)
        if self.agent is None:
            self.emit("reply", {"text": "The language model is not available right now."})
            return
        try:
            reply = explain.answer(self.agent, self.lang, text, context, [])
        except LLMError as e:
            reply = 'Language service unavailable. Please retry shortly.'
        self.emit("reply", {"text": reply})

    # ---------- views ----------
    def public_profile(self) -> dict:
        return {k: v for k, v in self.profile.items() if not k.startswith("_")}

    def snapshot(self) -> dict:
        return {"id": self.id, "state": self.state, "lang": self.lang, "profile": self.public_profile(),
                "pending_question": self.pending, "shortlist": self.shortlist}
