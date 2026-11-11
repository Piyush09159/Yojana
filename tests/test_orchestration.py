"""Agent orchestration: runner, the five agents' code checks, the orchestrator, the job lanes and
role configuration. Offline: mock models, fake search, pages built in memory."""
import json

import pytest

from agents.extractor import benefits_a_person
from agents.reader import READER
from agents.runner import AgentInvalid, AgentSpec, run_agent
from agents.scout import SCOUT
from agents.verifier import review
from discovery.discover import Discoverer, DiscoveryReport
from discovery.search import FakeSearch, SearchResult
from ingest.extract import drop_unsupported, flagged_rules, rule_needs_review, slugify
from ingest.fetch import Page
from llm import LLMError, reset_shared, shared_llm
from llm.config import ROLE_TIMEOUTS, resolve, role_timeout
from llm.providers.mock_provider import MockProvider
from saathi import reply
from saathi.orchestrator import Orchestrator
from saathi.short_answer import parse
from saathi.sufficiency import is_good, segment
from store import db, runtime
from worker import jobs
from worker.loop import run_one

WIDOW = {"age": 45, "gender": "female", "marital_status": "widowed", "social_category": "sc",
         "state": "maharashtra", "district": "beed", "occupation": "agricultural_labourer",
         "annual_family_income_inr": 20000}


def understood(**facts):
    return {"language": "en", "facts": facts, "intent": "give_info", "scheme_name": None}


# ---------- runner: never raises, degrades, caches, respects budget ----------

def spec(call, **kw):
    return AgentSpec(name=kw.pop("name", "t"), role="extractor", lane=kw.pop("lane", "realtime"), call=call, **kw)


def test_runner_turns_model_failure_into_a_result():
    def boom(llm, inp):
        raise LLMError("503 overloaded")
    r = run_agent(spec(boom), {}, llm=MockProvider())
    assert not r.ok and r.degraded and "503" in r.error


def test_runner_uses_the_code_fallback_when_models_fail():
    def boom(llm, inp):
        raise LLMError("429 quota")
    r = run_agent(spec(boom, degrade=lambda inp: {"from": "code"}), {}, llm=MockProvider())
    assert r.ok and r.degraded and r.data == {"from": "code"} and r.source == "fallback"


def test_runner_survives_a_bug_in_the_agent():
    def bug(llm, inp):
        return {}["missing"]
    r = run_agent(spec(bug), {}, llm=MockProvider())
    assert not r.ok and "KeyError" in r.error


def test_runner_rejects_invalid_output():
    def bad(output, inp):
        raise AgentInvalid("no verdicts")
    r = run_agent(spec(lambda llm, inp: {"x": 1}, validate=bad), {}, llm=MockProvider())
    assert not r.ok and "invalid output" in r.error


def test_runner_cache_hit_costs_nothing():
    calls = []

    def call(llm, inp):
        calls.append(1)
        return {"n": len(calls)}
    s = spec(call, name="cached", cache_key=lambda inp: "k1")
    first, second = run_agent(s, {}, llm=MockProvider()), run_agent(s, {}, llm=MockProvider())
    assert first.data == second.data == {"n": 1} and second.source == "cache" and len(calls) == 1


def test_runner_stops_at_the_lane_budget(monkeypatch):
    monkeypatch.setenv("BUDGET_TOKENS_GAP", "1000")
    runtime.budget_add("gap", "openai:x", calls=1, tokens=900)
    r = run_agent(spec(lambda llm, inp: "spent", lane="gap", est_tokens=500, degrade=lambda i: "code"), {},
                  llm=MockProvider())
    assert r.degraded and r.data == "code" and "budget" in r.error
    runtime.budget_add("gap", "local:qwen", calls=1, tokens=10_000)     # local models are free
    assert runtime.budget_used("gap")["tokens"] == 900


def test_runner_records_tokens_per_lane():
    mock = MockProvider(responses={"Person": understood(age=60)})
    r = run_agent(READER, {"text": "I am 60", "asked": None}, llm=mock)
    assert r.ok and r.tokens > 0
    assert runtime.budget_used("realtime", include_local=True)["tokens"] == r.tokens


# ---------- short answers are read by code ----------

@pytest.mark.parametrize("text,lang,value", [("ho", "mr", True), ("नाही", "mr", False), ("haan", "hi", True),
                                             ("No.", "en", False), ("जी हाँ", "hi", True)])
def test_yes_no_in_three_languages(text, lang, value):
    q = {"qid": "x", "field": "is_bpl", "fields": ["is_bpl"], "type": "yes_no"}
    assert parse(q, text, lang).value is value


def test_numbers_money_and_devanagari_digits():
    age = {"field": "age", "fields": ["age"], "type": "number"}
    income = {"field": "annual_family_income_inr", "fields": ["annual_family_income_inr"], "type": "number"}
    assert parse(age, "४५", "mr").value == 45
    assert parse(income, "40 hazar", "hi").value == 40000
    assert parse(income, "1.5 lakh", "en").value == 150000
    assert parse(income, "10 hazar mahina", "hi").kind == "none"             # monthly: the Reader converts it
    assert parse(age, "1.5 lakh", "en").kind == "none"                       # money words only for income


def test_choices_by_number_word_and_synonym():
    q = {"field": "residence", "fields": ["residence"], "type": "choice"}
    assert parse(q, "1", "en").value == "rural"
    assert parse(q, "शहर", "mr").value == "urban"
    g = {"field": "gender", "fields": ["gender"], "type": "choice"}
    assert parse(g, "महिला", "mr").value == "female"


def test_long_or_unclear_replies_go_to_the_reader():
    q = {"field": "is_bpl", "fields": ["is_bpl"], "type": "yes_no"}
    assert parse(q, "I think we have a yellow card but not sure", "en").kind == "none"
    assert parse(q, "पता नहीं", "hi").kind == "skip"
    d = {"field": "disability_percent", "fields": ["disability_percent"], "type": "number"}
    assert parse(d, "नाही", "mr").value == 0                                  # "no" disability -> 0


# ---------- replies are code templates ----------

def test_question_lists_translated_options_and_yes_no_hint():
    q = {"field": "residence", "fields": ["residence"], "type": "choice", "text_en": "Village or town?"}
    text = reply.question_text(q, "mr")
    assert "1. गाव" in text and "2. शहर" in text
    assert "(हो / नाही)" in reply.question_text({"field": "is_bpl", "fields": ["is_bpl"], "type": "yes_no",
                                                  "text_mr": "बीपीएल कार्ड आहे का?"}, "mr")


def test_results_carry_source_and_unverified_label():
    card = {"id": "s", "name": "Widow Pension", "annual_value_inr": 18000, "why_ranked": ["you meet 4"],
            "verified": False, "source_site": "sjsa.maharashtra.gov.in", "source_url": "https://sjsa.maharashtra.gov.in/x"}
    text = reply.results_text([card], "en", searching=True)
    assert "https://sjsa.maharashtra.gov.in/x" in text and "not yet checked by a person" in text
    assert "checking official government websites" in text


# ---------- sufficiency ----------

def test_good_match_needs_status_quality_and_match():
    solid = {"eligibility": {"all": [{"field": "gender", "op": "eq", "value": "female"},
                                     {"field": "age", "op": "gte", "value": 40},
                                     {"field": "marital_status", "op": "eq", "value": "widowed"}]},
             "benefit_summary": "Rs 1,500 per month", "documents": [{"name": "x"}], "how_to_apply": "tehsil",
             "last_verified": "2026-10-23"}
    assert is_good({"status": "eligible", "match": 5}, solid)[0]
    assert not is_good({"status": "near_miss", "match": 5}, solid)[0]
    assert not is_good({"status": "eligible", "match": 1.5}, solid)[0]          # matched on one generic rule
    assert not is_good({"status": "eligible", "match": 5}, {"eligibility": {"all": []}})[0]


def test_segment_groups_people_by_where_and_who():
    key, dims = segment(WIDOW)
    assert key == "maharashtra|beed|agricultural_labourer|female|sc|40-59" and dims["age_band"] == "40-59"


# ---------- the orchestrator ----------

def test_a_whole_conversation_uses_one_reader_call():
    reader = MockProvider(responses={"Person": understood(**WIDOW)})
    o = Orchestrator(reader_llm=reader, enqueue_gaps=False)
    t = o.handle("c1", "I am a 45 year old SC widow in Beed, farm labourer, income 20 thousand")
    for _ in range(8):
        if not t.question:
            break
        t = o.handle("c1", "1" if t.question["type"] == "choice" else ("0" if t.question["type"] == "number" else "no"))
    assert t.question is None                                                # questions done
    assert runtime.conversation_load("c1")["stage"] == "results"             # results shown (none fit the 3 test schemes)
    assert len(reader.calls) == 1                                            # every short reply read by code


def test_sensitive_numbers_are_refused_before_any_model():
    reader = MockProvider(responses={"Person": understood(age=60)})
    t = Orchestrator(reader_llm=reader).handle("c2", "my aadhaar 1234 5678 9012")
    assert "Aadhaar" in t.reply and reader.calls == []


def test_reader_outage_never_invents_facts():
    def down(*a, **k):
        raise LLMError("503 overloaded")
    reader = MockProvider()
    reader.complete = down
    t = Orchestrator(reader_llm=reader).handle("c3", "I am 60 and live in Maharashtra")
    assert t.degraded and t.profile == {} and "could not read" in t.reply.lower()


def test_details_by_number_after_results():
    reader = MockProvider(responses={"Person": understood(**WIDOW)})
    o = Orchestrator(reader_llm=reader, enqueue_gaps=False)
    t = o.handle("c4", "45 year old SC widow in Beed")
    while t.question:
        t = o.handle("c4", "skip")
    if t.cards:
        d = o.handle("c4", "1")
        assert t.cards[0]["name"].split()[0] in d.reply and "http" in d.reply


def test_free_question_goes_to_the_replier_and_invented_links_are_refused():
    reader = MockProvider(queue=[understood(**WIDOW),
                                 {"language": "en", "facts": {}, "intent": "ask_general", "scheme_name": None}])
    replier = MockProvider(queue=["See https://made-up.example.com for details"])
    o = Orchestrator(reader_llm=reader, replier_llm=replier, enqueue_gaps=False)
    o.handle("c5", "45 year old SC widow in Beed")
    t = o.handle("c5", "what documents do I need for these schemes?")
    assert t.degraded and "made-up.example.com" not in t.reply


def test_a_gap_queues_one_search_per_profile_group_per_day():
    reader = MockProvider(responses={"Person": understood(**WIDOW)})
    o = Orchestrator(reader_llm=reader)
    for chat in ("g1", "g2"):
        t = o.handle(chat, "45 year old SC widow in Beed")
        while t.question:
            t = o.handle(chat, "skip")
    assert runtime.job_counts().get("gapfill", {}).get("queued") == 1          # second person: no new search


def test_a_question_is_not_asked_forever():
    reader = MockProvider(responses={"Person": understood(state="maharashtra")})
    o = Orchestrator(reader_llm=reader, enqueue_gaps=False)
    first = o.handle("c6", "hello I live in Maharashtra")
    q = first.question["field"]
    o.handle("c6", "something unrelated about the weather today")
    third = o.handle("c6", "more unrelated words about the weather")
    assert third.question is None or third.question["field"] != q


# ---------- extraction checks: field-quote, verifier, person ----------

def test_field_quote_check_flags_the_kerala_mistakes():
    assert rule_needs_review({"field": "residence", "op": "eq", "value": "rural",
                              "source_quote": "Residing within the Kerala State for not less than three years."})
    assert rule_needs_review({"field": "is_bpl", "op": "eq", "value": True,
                              "source_quote": "No person shall be eligible if in receipt of any other pension."})
    assert rule_needs_review({"field": "age", "op": "gte", "value": 60, "source_quote": "Age of 60 years or higher."}) is None
    assert rule_needs_review({"field": "state", "op": "eq", "value": "kerala",
                              "source_quote": "Residing within the Kerala State"}) is None
    assert rule_needs_review({"field": "social_category", "op": "eq", "value": "sc",
                              "source_quote": "this scheme is for everyone"})              # "sc" is not in "scheme"


def scheme_with(rules, any_rules=None):
    return {"name": "Old Age Pension", "eligibility": {"all": rules, **({"any": any_rules} if any_rules else {})},
            "unmapped_criteria": []}


def test_unsupported_rules_move_to_check_yourself():
    s = scheme_with([{"field": "age", "op": "gte", "value": 60, "source_quote": "Age of 60 years or higher."},
                     {"field": "residence", "op": "eq", "value": "rural", "reason": "residing in Kerala",
                      "source_quote": "Residing within the Kerala State"}])
    assert drop_unsupported(s, flagged_rules(s)) == 1
    assert [r["field"] for r in s["eligibility"]["all"]] == ["age"]
    assert "residing in kerala" in s["unmapped_criteria"][0]


def test_dropping_one_any_alternative_drops_the_block():
    s = scheme_with([], [{"field": "occupation", "op": "eq", "value": "farmer", "source_quote": "for farmers"},
                         {"field": "residence", "op": "eq", "value": "rural", "source_quote": "in Kerala"}])
    drop_unsupported(s, [f for f in flagged_rules(s) if f["field"] == "residence"])
    assert "any" not in s["eligibility"]


def test_verifier_drops_what_it_does_not_support(monkeypatch):
    monkeypatch.setenv("VERIFY_RULES", "1")
    s = scheme_with([{"field": "residence", "op": "eq", "value": "rural", "source_quote": "Residing within Kerala"},
                     {"field": "family_size", "op": "gte", "value": 1, "source_quote": "Applicant must be a destitute."}])
    verifier = MockProvider(responses={"You check rules": {"verdicts": [
        {"i": 0, "supported": False, "reason": "domicile, not rural"}, {"i": 1, "supported": True}]}})
    out = review(s, llm=verifier)
    assert out == {"flagged": 2, "dropped": 1, "degraded": False, "source": "mock:mock"}


def test_verifier_outage_keeps_doubtful_rules_out(monkeypatch):
    monkeypatch.setenv("VERIFY_RULES", "1")
    s = scheme_with([{"field": "residence", "op": "eq", "value": "rural", "source_quote": "Residing within Kerala"}])
    out = review(s, llm=MockProvider())                                      # no canned reply: the model "fails"
    assert out["dropped"] == 1 and out["degraded"]


def test_village_programmes_are_not_schemes_for_people():
    assert not benefits_a_person({"benefit_recipient": "village", "name": "Gram Vikas Nidhi"})
    assert benefits_a_person({"benefit_recipient": "person", "name": "x"})
    assert not benefits_a_person({"name": "Construction of anganwadi centers", "benefit_summary": "for villages"})


def test_devanagari_names_get_readable_ids():
    slug = slugify("पेसा 5% निधी योजना")
    assert slug.startswith("pes") and "_5_" in slug and "nidh" in slug          # was "5" before


# ---------- the job lanes ----------

PAGE_TEXT = ("Widow Pension Scheme. The applicant should be a widow aged 40 years or above. "
             "Annual family income should not exceed Rs. 21,000. Apply at the tehsil office.")
URL = "https://sjsa.maharashtra.gov.in/en/scheme/widow-pension/"


class MemFetcher:
    def fetch(self, url):
        return Page(url=url, final_url=url, content_type="html", title="Widow Pension", text=PAGE_TEXT,
                    content_hash="h-widow", fetched_at="2026-11-11T10:00:00+00:00")


def extraction(**over):
    data = {"is_scheme_page": True, "name": "Widow Pension Scheme", "level": "state", "state": "Maharashtra",
            "benefit_summary": "Rs 1,500 per month", "benefit_annual_inr": 18000, "benefit_recipient": "person",
            "value_score": 4, "effort": 2, "how_to_apply": "Apply at the tehsil office.",
            "documents": ["Death certificate"], "mentioned_schemes": [], "any_rules": [], "unmapped_criteria": [],
            "all_rules": [
                {"field": "gender", "op": "eq", "value": "female", "source_quote": "The applicant should be a widow aged 40 years or above."},
                {"field": "marital_status", "op": "eq", "value": "widowed", "source_quote": "The applicant should be a widow aged 40 years or above."},
                {"field": "age", "op": "gte", "value": 40, "source_quote": "a widow aged 40 years or above"},
                {"field": "annual_family_income_inr", "op": "lte", "value": 21000,
                 "source_quote": "Annual family income should not exceed Rs. 21,000."}]}
    data.update(over)
    return data


def discoverer(llm, results=None):
    search = FakeSearch({"": results or [SearchResult("Widow Pension", URL, "")]})
    return Discoverer(llm=llm, search=search, fetcher=MemFetcher(), lane="gap")


def test_gapfill_plans_searches_and_queues_one_extract_per_page(monkeypatch):
    monkeypatch.setattr(jobs, "run_agent", lambda spec, inp, **k: run_agent(
        spec, inp, llm=MockProvider(responses={"Profile group": {
            "summary": "s", "needs": [{"need": "pension", "tags": ["widow"], "search_terms": ["widow pension"]}],
            "scheme_names": ["Widow Pension Scheme"]}})))
    seg, _ = segment(WIDOW)
    out = jobs.gapfill({"segment": seg, "profile": WIDOW}, discoverer=discoverer(MockProvider()))
    assert out["searches"] == jobs.MAX_SEARCHES_PER_GAP                     # name query + need query
    assert out["pages_queued"] == 1                                          # same page found twice: one job
    assert runtime.job_counts()["extract"]["queued"] == 1
    assert runtime.budget_used("gap")["searches"] == jobs.MAX_SEARCHES_PER_GAP


def test_gapfill_respects_the_daily_search_budget(monkeypatch):
    monkeypatch.setenv("BUDGET_SEARCHES_DAY", "0")
    seg, _ = segment(WIDOW)
    out = jobs.gapfill({"segment": seg, "profile": WIDOW}, discoverer=discoverer(MockProvider()))
    assert out["searches"] == 0 and out["pages_queued"] == 0


def test_extract_saves_and_then_notifies_people_like_this():
    seg, _ = segment(WIDOW)
    runtime.conversation_save("w1", {"profile": WIDOW, "asked": [], "pending": None, "lang": "en",
                                     "stage": "results", "shown": [], "segment": seg, "followups": []})
    d = discoverer(MockProvider(responses={"Page URL": extraction()}))
    out = jobs.extract({"url": URL, "segment": seg, "lane": "gap"}, discoverer=d)
    assert out["new"] and db.get_scheme(out["scheme"])["benefit_recipient"] == "person"
    told = jobs.notify({"segment": seg})
    state = runtime.conversation_load("w1")
    assert told["conversations_told"] == 1 and "Widow Pension Scheme" in state["followups"][0]["text"]
    assert jobs.notify({"segment": seg})["conversations_told"] == 0           # not told twice


def test_extract_retries_when_the_model_is_down():
    d = discoverer(MockProvider())                                           # no canned reply: extraction fails
    with pytest.raises(jobs.RetryLater):
        jobs.extract({"url": URL}, discoverer=d)
    assert db.url_record(URL) is None                                        # not marked done: read again later


def test_village_page_is_recorded_not_saved():
    d = discoverer(MockProvider(responses={"Page URL": extraction(benefit_recipient="village")}))
    out = jobs.extract({"url": URL}, discoverer=d)
    assert out["scheme"] is None and out["not_for_people"] == 1
    assert db.url_record(URL)["status"] == "not_scheme"


def test_worker_retries_then_gives_up_and_keeps_the_error():
    job = runtime.job_enqueue("explode", {}, max_attempts=2)

    def explode(payload):
        raise ValueError("bad data")
    for _ in range(2):
        r = run_one(["explode"], {"explode": explode})
        runtime._run("UPDATE job SET run_after = 0 WHERE id = %s", (job,))   # skip the wait
    assert r["status"] == "failed" and "bad data" in runtime.job_get(job)["error"]


def test_a_dead_workers_job_is_taken_again():
    job = runtime.job_enqueue("gapfill", {"x": 1})
    runtime.job_claim(["gapfill"], lease=-1)                                 # claimed, lease already over
    again = runtime.job_claim(["gapfill"])
    assert again["id"] == job and again["attempts"] == 2


# ---------- roles: config, timeouts, shared clients ----------

def test_agent_roles_fall_back_to_older_role_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("EXTRACTOR_PROVIDER", "gemini")
    monkeypatch.setenv("EXTRACTOR_MODEL", "gemini-x")
    assert resolve("reader") == ("gemini", "gemini-x")                      # uses extractor settings
    monkeypatch.setenv("READER_PROVIDER", "openai")
    assert resolve("reader") == ("openai", "gpt-4.1-mini")                  # own provider: not gemini-x


def test_every_role_has_a_timeout(monkeypatch):
    assert role_timeout("reader") == ROLE_TIMEOUTS["reader"] < role_timeout("extractor")
    monkeypatch.setenv("READER_TIMEOUT", "7")
    assert role_timeout("reader") == 7.0


def test_shared_client_is_reused_so_cooldowns_persist(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    reset_shared()
    assert shared_llm("reader") is shared_llm("reader")
    assert shared_llm("reader") is not shared_llm("scout")


def test_fast_roles_do_not_wait_on_a_busy_provider(monkeypatch):
    from llm.config import get_llm
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("READER_FALLBACKS", "mock:other")
    monkeypatch.setenv("SCOUT_FALLBACKS", "mock:other")
    assert get_llm("reader").busy_tries == 1 and get_llm("scout").busy_tries == 3


def test_scout_degrades_to_needs_from_the_profile():
    r = run_agent(SCOUT, WIDOW, llm=MockProvider())                         # model "fails"
    assert r.ok and r.degraded and any("widow" in n["tags"] for n in r.data["needs"]) and r.data["scheme_names"] == []
    assert json.dumps(r.data)                                               # cacheable JSON


def test_a_slow_provider_is_skipped_at_once_not_retried():
    """A 504 / timeout means the provider already used its whole time limit: move on, do not wait."""
    from llm.fallback import FallbackClient

    class Slow:
        name, role, calls = "gemini:slow", "extractor", 0

        def complete(self, messages, **kw):
            Slow.calls += 1
            raise LLMError("gemini:slow: ServerError: 504 DEADLINE_EXCEEDED")
    waits = []
    fb = FallbackClient(Slow(), MockProvider(queue=["ok"]), notify=lambda m: None, sleep=waits.append)
    assert fb.complete([{"role": "user", "content": "q"}]).text == "ok"
    assert Slow.calls == 1 and waits == []                       # one try, no backoff sleeps
    assert fb.active.name == "mock:mock"                          # the slow one rests


@pytest.mark.parametrize("spelling,key", [("bhid", "beed"), ("बीड", "beed"), ("Bid", "beed"), ("Nasik", "nashik"),
                                          ("अहिल्यानगर", "ahmednagar"), ("Puné", "pune"), ("Mysuru", "mysuru")])
def test_districts_spelled_differently_resolve(spelling, key):
    from core.fields import check_value
    assert check_value("district", spelling) == key          # unknown districts (Mysuru) are kept as given


def test_failed_jobs_can_be_sent_back():
    job = runtime.job_enqueue("extract", {"url": "u"}, max_attempts=1)
    runtime.job_claim(["extract"])
    assert runtime.job_fail(job, "504") == "failed"
    assert runtime.job_retry_failed("extract") == 1
    again = runtime.job_claim(["extract"])
    assert again["id"] == job and again["attempts"] == 1
