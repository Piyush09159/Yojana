"""The agent-orchestration hooks inside the refactor conversation flow (web/conversation_api.py):
shared Reader client, short answers read by code, and the background gap search. Offline."""
from types import SimpleNamespace

from llm import shared_llm
from store import runtime
from web import conversation_api as api


def understood(**facts):
    return SimpleNamespace(facts=facts, language="en", intent="give_info")


def test_reader_client_is_shared_across_messages(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert api.get_llm() is api.get_llm("extractor") is shared_llm("reader")


def test_short_answers_are_read_by_code():
    yes_no = {"field": "is_bpl", "fields": ["is_bpl"], "type": "yes_no"}
    assert api._short_answer(yes_no, "ho", "mr").facts == {"is_bpl": True}
    age = {"field": "age", "fields": ["age"], "type": "number"}
    assert api._short_answer(age, "45", "en").facts == {"age": 45}
    assert api._short_answer(age, "I think I am around forty five years old", "en") is None   # model reads it
    combined = {"field": "x", "fields": ["family_income_tax_payer", "family_govt_employee_excluded"], "type": "yes_no"}
    assert api._short_answer(combined, "no", "en") is None                                    # multi-field: model
    assert api._short_answer(None, "45", "en") is None


def test_a_short_reply_in_the_conversation_makes_no_model_call(monkeypatch):
    calls = []

    def fake_understand(llm, text, pending, context=None):
        calls.append(text)
        return understood(age=60, state="maharashtra")
    monkeypatch.setattr(api, "get_llm", lambda role="reader": object())
    monkeypatch.setattr(api, "understand", fake_understand)
    first = api.conversation(api.Message(text="I am 60 and live in Maharashtra"))
    q = first["question"]
    assert q is not None and q["type"] in ("yes_no", "number", "choice")
    if q["type"] == "choice":
        from core.fields import load_fields
        reply = load_fields()[q["field"]]["values"][0].replace("_", " ")   # e.g. "farmer"
    else:
        reply = "yes" if q["type"] == "yes_no" else "2"
    second = api.conversation(api.Message(text=reply, session_id=first["session_id"]))
    assert calls == ["I am 60 and live in Maharashtra"]           # the reply never reached the model
    assert q["field"] in second["profile"]


def test_thin_results_queue_one_background_search_per_profile_group():
    from discovery.catalog import catalog_for
    from saathi.shortlist import build_shortlist
    profile = {"age": 45, "gender": "female", "marital_status": "widowed", "state": "maharashtra", "district": "beed"}
    catalog = catalog_for(profile)
    shortlist = build_shortlist({**profile, "_asked": []}, catalog)
    assert api._queue_gap_search(profile, shortlist, catalog) is True          # 3 test schemes: not enough
    assert api._queue_gap_search(profile, shortlist, catalog) is False         # same group today: no repeat
    assert runtime.job_counts()["gapfill"]["queued"] == 1


def test_gap_search_never_breaks_a_reply(monkeypatch):
    monkeypatch.setattr(runtime, "job_enqueue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert api._queue_gap_search({"state": "maharashtra"}, {"start_with": None, "others": []}, {}) is False
    assert api._queue_gap_search({}, {"start_with": None}, {}) is False                     # no state: nothing
