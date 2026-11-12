"""The conversation orchestrator: one deterministic state machine per conversation, used by the
terminal (python -m saathi) and the worker's notify lane. NOTE (audit 2026-11-11): WhatsApp and the
/app API do NOT run through this class yet; they use web/conversation_api.py, which shares its
building blocks (shared Reader client, short_answer, gap queue). See docs/PRODUCTION_READINESS_AUDIT.md.
Code runs the flow; agents are called only for the narrow jobs code cannot do, through agents.runner.run_agent, which never raises.

One turn:
    guard        Aadhaar / bank / phone numbers are refused before anything else
    commands     "restart"; "1".."5" after results -> that scheme's details (code)
    read         short answer to the pending question -> code (saathi/short_answer.py), 0 tokens
                 anything else -> Reader agent (facts + intent); facts validated by core/fields
    answer       a free question about schemes -> Replier agent, given only engine JSON
    decide       catalog for the person's state/district (cached) -> engine -> rank   (core/, no LLM)
    next         the most valuable unanswered question (core/questions), at most MAX_QUESTIONS
                 otherwise the top 5 (saathi/sufficiency.pick): good matches first
    gap          fewer than 5 good matches -> one gapfill job per profile group per day (worker/)
    reply        built by code in en/hi/mr (saathi/reply.py)

State lives in the conversation table (store/runtime.py): profile, questions asked, the pending
question, language, last shown schemes, segment, follow-ups. No message text is stored.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from agents.reader import READER
from agents.replier import REPLIER
from agents.runner import run_agent
from core.fields import FieldError, check_value, validate_profile
from core.questions import apply_answer, question_candidates
from discovery.catalog import catalog_for
from saathi import reply
from saathi.guard import REFUSAL, sensitive_kind
from saathi.short_answer import parse
from saathi.shortlist import build_shortlist, find_scheme, scheme_details
from saathi.sufficiency import TARGET, pick, segment
from store import db, runtime

MAX_QUESTIONS = 6
MAX_TRIES = 2                                 # unanswered replies before a question is skipped
MIN_QUESTION_SCORE = 1.0
CANDIDATES = 15                               # ranked schemes considered before picking the top 5
GAP_EVERY = runtime.DAY                       # search the web at most once a day per profile group
CATALOG_TTL = 60.0                            # seconds; other processes (the worker) may add schemes
RESTART = {"restart", "start over", "reset", "new", "नवीन", "फिर से", "फिरसे", "पुन्हा सुरू", "शुरू से"}
# acknowledgements after results: answered by code (show results and any follow-ups), no Reader call
ACK = {"ok", "okay", "k", "thanks", "thank you", "thx", "fine", "good", "hmm", "ji", "dhanyavad",
       "धन्यवाद", "ठीक", "ठीक है", "बरं", "बर", "छान", "शुक्रिया", "आभार"}
DIGIT = re.compile(r"^\s*([1-9])\s*$")


@dataclass
class Turn:
    chat_ref: str
    reply: str
    lang: str
    profile: dict
    question: dict | None = None             # the pending question, if one was asked
    cards: list[dict] = field(default_factory=list)
    shortlist: dict | None = None             # full engine result (the /app dashboard renders it)
    good: int = 0
    corrections: dict = field(default_factory=dict)
    gap_job: int | None = None
    degraded: bool = False                    # a model failed and code answered instead
    agents: list[dict] = field(default_factory=list)   # which agents ran, source, tokens, ms


def new_state(lang: str = "en") -> dict:
    return {"profile": {}, "asked": [], "pending": None, "lang": lang, "stage": "profiling",
            "shown": [], "segment": None, "followups": []}


def _public(profile: dict) -> dict:
    return {k: v for k, v in profile.items() if not k.startswith("_")}


def _pending_from(candidate: dict) -> dict:
    """The question to remember between turns: what reply.py needs to word it and short_answer.py
    needs to read the answer."""
    q = candidate["question"]
    return {"qid": q["id"], "field": candidate["field"], "fields": list(q["fields"]), "type": q.get("type", "text"),
            "choices": list(q.get("choices") or []), "on_no": q.get("on_no"),
            "text_en": q.get("text_en", ""), "text_hi": q.get("text_hi", ""), "text_mr": q.get("text_mr", ""),
            "reason": "This decides " + ", ".join(candidate["schemes"][:2]),
            "schemes": candidate["schemes"][:5]}


def _irrelevant(candidate: dict, profile: dict) -> bool:
    """Questions that would sound absurd for this person (asking a 34-year-old worker whether
    they are a student); the engine still treats the field as unknown, never as answered."""
    f = candidate["field"]
    age = profile.get("age")
    if f in ("is_student", "current_study_level") and isinstance(age, (int, float)) and age >= 30:
        return True
    if f == "has_girl_child_under_10" and isinstance(age, (int, float)) and age < 18:
        return True
    if f == "is_pregnant_or_lactating" and (profile.get("gender") == "male"
                                             or (isinstance(age, (int, float)) and not 15 <= age < 45)):
        return True
    return False


class Orchestrator:
    def __init__(self, reader_llm=None, replier_llm=None, enqueue_gaps: bool = True):
        self.reader_llm = reader_llm          # None: the role's shared client (tests pass a mock)
        self.replier_llm = replier_llm
        self.enqueue_gaps = enqueue_gaps
        self._catalogs: dict[tuple, tuple[float, int, dict]] = {}

    # ---------- catalog cache ----------
    def catalog(self, profile: dict) -> dict:
        key = (profile.get("state"), profile.get("district"))
        hit = self._catalogs.get(key)
        if hit and time.monotonic() - hit[0] < CATALOG_TTL and hit[1] == db.version():
            return hit[2]
        cat = catalog_for(profile)
        self._catalogs[key] = (time.monotonic(), db.version(), cat)
        return cat

    # ---------- one turn ----------
    def handle(self, chat_ref: str, text: str, lang: str | None = None, reply_to: dict | None = None) -> Turn:
        """One turn. reply_to says how to reach this person later (e.g. {"channel": "waha",
        "chat_id": ...}) so the gap lane can send follow-ups; kept in the conversation state."""
        state = runtime.conversation_load(chat_ref) or new_state(lang or "en")
        if reply_to:
            state["reply_to"] = reply_to
        lang = state.get("lang") or lang or "en"
        agents: list[dict] = []
        degraded = False

        kind = sensitive_kind(text)                                        # guard
        if kind:
            return Turn(chat_ref, REFUSAL[lang if lang in REFUSAL else "en"].format(kind=kind), lang,
                        _public(state["profile"]), question=state.get("pending"))

        t = (text or "").strip()
        if t.lower() in RESTART:                                           # commands
            state = new_state(lang)
            runtime.conversation_save(chat_ref, state)
            return self._next(chat_ref, state, agents, False, {}, prefix="")
        if state.get("stage") == "results" and not state.get("pending") and t.lower().strip(" .!") in ACK:
            return self._next(chat_ref, state, agents, False, {})
        m = DIGIT.match(t)
        if m and state.get("stage") == "results" and not state.get("pending") and state.get("shown"):
            idx = int(m.group(1)) - 1
            if idx < len(state["shown"]):
                return self._details(chat_ref, state, state["shown"][idx])

        profile = dict(state["profile"])
        profile["_asked"] = list(state.get("asked") or [])
        pending = state.get("pending")
        facts: dict = {}
        intent, scheme_name = "give_info", None

        parsed = parse(pending, t, lang)                                   # read: code first
        if parsed.kind == "value":
            q = {"id": pending["qid"], "type": pending["type"], "fields": pending["fields"],
                 "on_no": pending.get("on_no")}
            try:
                value = parsed.value if q["type"] == "yes_no" and len(q["fields"]) > 1 else \
                    check_value(q["fields"][0], parsed.value)
                # apply_answer handles combined questions ("no" to an exclusions question sets all
                # of its fields); questions asked are tracked in state["asked"], not in the profile
                updated = apply_answer(profile, q, value)
                facts = {k: v for k, v in updated.items() if not k.startswith("_") and profile.get(k) != v}
                profile = updated
            except FieldError:
                parsed = parse(None, t, lang)
        if parsed.kind == "skip":
            pass
        elif parsed.kind != "value":                                       # read: Reader agent
            res = run_agent(READER, {"text": t, "asked": pending}, llm=self.reader_llm)
            agents.append({"agent": "reader", "source": res.source, "tokens": res.tokens, "ms": res.ms,
                           "ok": res.ok, "error": res.error})
            if not res.ok:
                # never guess facts: ask again, simply
                degraded = True
                runtime.conversation_save(chat_ref, state)
                again = reply.question_text(pending, lang) if pending else ""
                return Turn(chat_ref, f"{reply.w('reask', lang)}\n{again}".strip(), lang, _public(state["profile"]),
                            question=pending, degraded=True, agents=agents)
            u = res.data
            if len(t.split()) >= 3 or not state["profile"]:
                lang = u.language if u.language in reply.LANGS else lang
            intent, scheme_name = u.intent, u.scheme_name
            if intent == "restart":
                state = new_state(lang)
                runtime.conversation_save(chat_ref, state)
                return self._next(chat_ref, state, agents, False, {}, prefix="")
            facts = dict(u.facts)

        corrections = {k: {"previous": state["profile"][k], "new": v} for k, v in facts.items()
                       if k in state["profile"] and state["profile"][k] != v}
        clean, _ = validate_profile(facts)
        profile.update(clean)
        answered = pending and (parsed.kind in ("value", "skip") or any(f in clean for f in pending["fields"])
                                or intent == "dont_know")
        state["profile"] = _public(profile)
        state["lang"] = lang
        if answered:
            state["pending"] = None
            state["tries"] = 0
        elif pending:
            # never ask the same question forever: after a second reply that does not answer it, skip it
            state["tries"] = state.get("tries", 0) + 1
            if state["tries"] >= MAX_TRIES:
                state["pending"], state["tries"] = None, 0
        if intent in ("ask_about_scheme", "ask_general") and not clean:    # answer: Replier agent
            return self._answer(chat_ref, state, t, scheme_name, agents)
        return self._next(chat_ref, state, agents, degraded, corrections)

    # ---------- decide what to say ----------
    def _next(self, chat_ref, state, agents, degraded, corrections, prefix: str | None = None) -> Turn:
        lang = state["lang"]
        profile = dict(state["profile"])
        catalog = self.catalog(profile)
        shortlist = build_shortlist({**profile, "_asked": []}, catalog, n_others=CANDIDATES)
        # follow-ups from the gap lane that could not be sent on their own are shown first
        unsent = [f["text"] for f in state.get("followups") or [] if not f.get("delivered")]
        head = "\n\n".join(unsent) if prefix is None else ""
        state["followups"] = []

        pending = state.get("pending")
        if pending is None and len(state["asked"]) < MAX_QUESTIONS:
            cands = [c for c in question_candidates({**profile, "_asked": state["asked"]}, catalog, limit=8)
                     if c["score"] >= MIN_QUESTION_SCORE and not _irrelevant(c, profile)]
            if cands:
                pending = _pending_from(cands[0])
                state["asked"].append(pending["qid"])
                state["pending"] = pending
        if pending is not None:
            state["stage"] = "profiling"
            runtime.conversation_save(chat_ref, state)
            text = reply.question_text(pending, lang)
            return Turn(chat_ref, f"{head}\n\n{text}".strip(), lang, profile, question=pending, shortlist=shortlist,
                        corrections=corrections, degraded=degraded, agents=agents)

        cards, good = pick(shortlist, catalog, TARGET)
        seg_key, dims = segment(profile)
        state.update(stage="results", shown=[c["id"] for c in cards], segment=seg_key)
        gap_job = None
        if good < TARGET and profile.get("state"):
            gap_job = self._enqueue_gap(chat_ref, seg_key, profile)
        try:
            runtime.coverage_log(seg_key, dims, good, web_triggered=gap_job is not None)
        except Exception:
            pass                                                          # bookkeeping never blocks a reply
        runtime.conversation_save(chat_ref, state)
        text = reply.results_text(cards, lang, searching=gap_job is not None)
        return Turn(chat_ref, f"{head}\n\n{text}".strip(), lang, profile, cards=cards, shortlist=shortlist, good=good,
                    corrections=corrections, gap_job=gap_job, degraded=degraded, agents=agents)

    def _enqueue_gap(self, chat_ref: str, seg_key: str, profile: dict) -> int | None:
        if not self.enqueue_gaps or runtime.segment_searched_within(seg_key, GAP_EVERY):
            return None
        job = runtime.job_enqueue("gapfill", {"segment": seg_key, "profile": _public(profile)},
                                  dedupe_key=f"gapfill:{seg_key}")
        if job is not None:
            runtime.segment_mark(seg_key, 0)       # marked now, so a burst of users queues one search
        return job

    def _details(self, chat_ref: str, state: dict, scheme_id: str) -> Turn:
        catalog = self.catalog(state["profile"])
        if scheme_id not in catalog:
            return self._next(chat_ref, state, [], False, {})
        details = scheme_details(scheme_id, {**state["profile"], "_asked": []}, catalog)
        runtime.conversation_save(chat_ref, state)
        return Turn(chat_ref, reply.details_text(details, state["lang"]), state["lang"], dict(state["profile"]))

    def _answer(self, chat_ref, state, question, scheme_name, agents) -> Turn:
        catalog = self.catalog(state["profile"])
        profile = {**state["profile"], "_asked": []}
        shortlist = build_shortlist(profile, catalog)
        context = {"profile": state["profile"], "shortlist": shortlist}
        sid = find_scheme(scheme_name or question, catalog)
        if sid:
            context["scheme_details"] = scheme_details(sid, profile, catalog)
        res = run_agent(REPLIER, {"lang": state["lang"], "question": question, "context": context},
                        llm=self.replier_llm)
        agents.append({"agent": "replier", "source": res.source, "tokens": res.tokens, "ms": res.ms,
                       "ok": res.ok, "error": res.error})
        runtime.conversation_save(chat_ref, state)
        text = res.data if res.ok else reply.w("none", state["lang"])
        return Turn(chat_ref, text, state["lang"], dict(state["profile"]), question=state.get("pending"),
                    shortlist=shortlist, degraded=res.degraded, agents=agents)

    def forget(self, chat_ref: str) -> None:
        runtime.conversation_delete(chat_ref)
