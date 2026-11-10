"""Web app, investigator and reasoned questions, offline: mock LLMs, fake discovery, no network."""
import time

import pytest
from fastapi.testclient import TestClient

from core.awareness import awareness, is_flagship
from core.loader import load_schemes
from core.questions import question_candidates
from core.ranking import lesser_known
from core.eligibility import evaluate_scheme
from llm.providers.mock_provider import MockProvider
from saathi.investigator import Budget, Investigator, fallback_analysis
from saathi.questioner import next_question
from web.app import create_app

from test_discovery import _discovered


def catalog_with_local():
    zp = _discovered(id="zp_widow_beed", name="ZP Beed Widow Self-employment Grant", district="beed",
                     agency_type="district", agency_name="Zilla Parishad Beed", benefit_annual_inr=25000)
    dept = _discovered(id="dept_widow", name="Dept Widow Assistance", agency_type="state_department",
                       eligibility={"all": [{"field": "gender", "op": "eq", "value": "female", "reason": "women"},
                                            {"field": "has_eshram_card", "op": "eq", "value": True,
                                             "reason": "registered on e-Shram"}]})
    return {**load_schemes(), zp["id"]: zp, dept["id"]: dept}


PROFILE = {"state": "maharashtra", "district": "beed", "age": 45, "gender": "female",
           "marital_status": "widowed", "annual_family_income_inr": 20000, "social_category": "sc",
           "occupation": "agricultural_labourer", "owns_cultivable_land": False, "is_bpl": True,
           "in_pmjay_beneficiary_list": False}


# ---------- awareness ----------

def test_awareness_levels():
    cat = catalog_with_local()
    assert is_flagship("Pradhan Mantri Kisan Samman Nidhi")
    assert awareness(cat["pm_kisan"]) == "high"
    assert awareness(cat["zp_widow_beed"]) == "low"
    assert awareness(cat["dept_widow"]) == "medium"


def test_lesser_known_excludes_flagships_and_prefers_obscure():
    cat = catalog_with_local()
    results = [evaluate_scheme(s, PROFILE) for s in cat.values()]
    ids = [r.scheme_id for r in lesser_known(results, cat)]
    assert ids[0] == "zp_widow_beed"
    assert "ignoaps" not in ids                       # flagship old-age pension is never "lesser-known"


def test_district_rule():
    s = catalog_with_local()["zp_widow_beed"]
    assert evaluate_scheme(s, PROFILE).status == "eligible_check_manually"  # discovered, not independently verified
    assert evaluate_scheme(s, {**PROFILE, "district": "pune"}).status == "ineligible"


# ---------- reasoned questions ----------

def test_question_candidates_value_of_information():
    cat = catalog_with_local()
    c = question_candidates(PROFILE, cat)
    assert c[0]["field"] == "has_eshram_card"
    assert "Dept Widow Assistance" in c[0]["schemes"]


def test_questioner_rejects_fields_outside_candidates():
    cat = catalog_with_local()
    planner = MockProvider(responses={"": {"field": "aadhaar_number", "question": "?", "reason": "x"}})
    q = next_question(planner, "en", {**PROFILE, "_asked": []}, cat)
    assert q["field"] == "has_eshram_card" and q["type"] == "bool"
    good = MockProvider(responses={"": {"field": "has_eshram_card", "question": "Do you have an e-Shram card?",
                                        "reason": "It decides Dept Widow Assistance."}})
    q = next_question(good, "en", {**PROFILE, "_asked": []}, cat)
    assert q["question"] == "Do you have an e-Shram card?" and "Dept Widow" in q["reason"]


# ---------- investigator ----------

def test_fallback_analysis_reads_the_form():
    tags = {t for n in fallback_analysis(PROFILE)["needs"] for t in n["tags"]}
    assert {"sc", "women", "widow"} <= tags


def test_investigator_without_web_only_analyses():
    events = []
    analysis = {"summary": "A widow in Beed.", "needs": [
        {"need": "income support", "why": "widowed labourer", "tags": ["widow"], "search_terms": ["widow pension"]}]}
    inv = Investigator(MockProvider(responses={"": analysis}), discoverer=None,
                       emit=lambda k, d: events.append(k)).run(PROFILE)
    assert inv.analysis["summary"] == "A widow in Beed." and events == ["thinking"]


# ---------- web app ----------

@pytest.fixture
def client():
    planner = MockProvider(responses={
        "Profile:": {"summary": "A 45-year-old SC widow in Beed.",
                     "needs": [{"need": "income", "why": "widow", "tags": ["widow"], "search_terms": ["widow"]}]},
        "candidates": {"field": "has_eshram_card", "question": "Do you have an e-Shram card?",
                       "reason": "It decides Dept Widow Assistance."}})
    agent = MockProvider(responses={"": "You need a death certificate and income certificate."})
    app = create_app(planner=planner, extractor=MockProvider(responses={"": "{}"}), agent=agent,
                     discoverer_factory=lambda: None, catalog_loader=catalog_with_local,
                     budget=Budget(rounds=1, searches=0, pages=0, seconds=5))
    return TestClient(app)


def wait_for(client, sid, kind, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        evs = client.get(f"/api/sessions/{sid}/events", params={"once": True}).json()
        if any(e["kind"] == kind for e in evs):
            return evs
        time.sleep(0.05)
    raise AssertionError(f"no {kind} event; got {[e['kind'] for e in evs]}")


def test_form_spec(client):
    spec = client.get("/api/form").json()
    names = [f["name"] for f in spec["fields"]]
    assert names[:3] == ["state", "district", "age"] and "beed" in spec["districts"]["maharashtra"]


def test_invalid_form_rejected(client):
    r = client.post("/api/sessions", json={"profile": {"age": -4, "state": "bombay"}, "web": False})
    assert r.status_code == 422


def test_full_session_flow(client):
    r = client.post("/api/sessions", json={"profile": {**{k: v for k, v in PROFILE.items()
                                                        if k not in ("is_bpl", "in_pmjay_beneficiary_list")},
                                                     "district": "Beed"}, "lang": "en", "web": False})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["profile"]["district"] == "beed"
    evs = wait_for(client, sid, "question")
    kinds = [e["kind"] for e in evs]
    assert kinds[0] == "results" and "thinking" in kinds
    q = [e for e in evs if e["kind"] == "question"][-1]["data"]
    assert q["field"] == "has_eshram_card"
    first_results = [e for e in evs if e["kind"] == "results"][-1]["data"]
    assert "zp_widow_beed" in [c["id"] for c in first_results["lesser_known"]]
    # structured answer is validated by code, no LLM
    assert client.post(f"/api/sessions/{sid}/answer", json={"field": "has_eshram_card", "value": "maybe"}).status_code == 422
    assert client.post(f"/api/sessions/{sid}/answer", json={"field": "has_eshram_card", "value": True}).json() == \
        {"stored": {"has_eshram_card": True}}
    time.sleep(0.3)
    snap = client.get(f"/api/sessions/{sid}").json()
    ids = [c["id"] for c in snap["shortlist"]["lesser_known"]]
    assert "dept_widow" in ids
    # free question goes to the agent with scheme context
    client.post(f"/api/sessions/{sid}/ask", json={"text": "What documents for ZP Beed Widow Self-employment Grant?"})
    evs = wait_for(client, sid, "reply")
    assert "death certificate" in [e for e in evs if e["kind"] == "reply"][-1]["data"]["text"]


def test_scheme_transparency_endpoint(client):
    s = client.get("/api/schemes/zp_widow_beed").json()
    assert s["awareness"] == "low" and s["rules"] and s["rules"][0]["quote"]
    assert client.get("/api/schemes/nope").status_code == 404


def test_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and 'data-site="public-landing"' in r.text
    assert 'id="profile-form"' not in r.text
    assert client.get("/static/app.js").status_code == 200


def test_scope_rules_are_not_evidence_that_we_know_who_qualifies():
    """A girl-child scheme whose only rules are "Maharashtra, Sangli" must not look eligible to a man."""
    girl_scheme = _discovered(
        id="zp_kanya", name="ZP Kanya Kalyan Yojana", district="sangli", agency_type="district",
        eligibility={"all": [{"field": "state", "op": "eq", "value": "maharashtra", "reason": "ZP Sangli"},
                             {"field": "district", "op": "eq", "value": "sangli", "reason": "ZP Sangli"}]},
        unmapped_criteria=["Families having one or two girl children"])
    man = {"state": "maharashtra", "district": "sangli", "gender": "male", "age": 28}
    assert evaluate_scheme(girl_scheme, man).status == "check_manually"
    # one real qualifying rule is enough evidence to call it likely
    with_rule = {**girl_scheme,
                 "eligibility": {"all": girl_scheme["eligibility"]["all"]
                                 + [{"field": "has_girl_child_under_10", "op": "eq", "value": True,
                                     "reason": "girl child"}]}}
    family = {**man, "has_girl_child_under_10": True}
    assert evaluate_scheme(with_rule, family).status == "eligible_check_manually"
