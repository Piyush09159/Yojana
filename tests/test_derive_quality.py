"""store/derive.py (structured rows from a scheme dict) and core/quality.py (record quality).
Pure functions: no database, no LLM."""
from datetime import date

from core.quality import quality_of
from store.derive import categories_of, derive, level_of, targets_of


def scheme(**over):
    base = {
        "id": "test_scheme", "name": "Test Widow Pension Scheme", "origin": "discovered", "verified": False,
        "state": "maharashtra", "district": None, "source_url": "https://sjsa.maharashtra.gov.in/x",
        "source_site": "sjsa.maharashtra.gov.in", "last_verified": "2026-10-23",
        "benefit_summary": "Rs 1,500 per month pension", "benefit_annual_inr": 0,
        "rank": {"value_score": 3, "effort": 3},
        "eligibility": {"all": [
            {"field": "gender", "op": "eq", "value": "female", "source_quote": "Only women may apply"},
            {"field": "marital_status", "op": "eq", "value": "widowed", "source_quote": "widows"},
            {"field": "age", "op": "gte", "value": 40, "source_quote": "aged 40 and above"},
            {"field": "age", "op": "lte", "value": 65, "source_quote": "up to 65 years"},
            {"field": "annual_family_income_inr", "op": "lte", "value": 21000, "source_quote": "income below"},
            {"field": "family_income_tax_payer", "op": "eq", "value": False, "source_quote": "not a taxpayer"},
            {"field": "state", "op": "eq", "value": "maharashtra", "source_quote": "resident of Maharashtra"},
        ]},
        "unmapped_criteria": [], "documents": [{"name": "Death certificate of husband", "key": "death_cert"}],
        "how_to_apply": "Apply at the tehsil office.", "actions": {}, "status_map": {},
    }
    base.update(over)
    return base


# ---------- targets: who a scheme is for, derived from rules ----------

def test_targets_come_from_rules_that_let_people_in():
    t = targets_of(scheme())
    assert ("gender", "female") in t
    assert ("marital_status", "widowed") in t
    assert ("income_limit", "up_to_1l") in t


def test_age_range_becomes_every_band_it_overlaps():
    t = targets_of(scheme())
    assert {v for d, v in t if d == "age_band"} == {"40-59", "60+"}       # 40..65


def test_exclusions_and_scope_are_not_targets():
    dims = {d for d, _ in targets_of(scheme())}
    assert "family_income_tax_payer" not in dims     # "not a taxpayer" keeps people out
    assert "state" not in dims and "district" not in dims


def test_in_list_gives_one_target_per_value():
    s = scheme(eligibility={"all": [{"field": "social_category", "op": "in", "value": ["sc", "st"]}]})
    assert targets_of(s) == [("social_category", "sc"), ("social_category", "st")]


def test_not_in_is_an_exclusion():
    s = scheme(eligibility={"all": [{"field": "social_category", "op": "not_in", "value": ["general"]}]})
    assert targets_of(s) == []


def test_bool_true_is_a_target_and_disability_threshold_is_a_flag():
    s = scheme(eligibility={"all": [{"field": "is_bpl", "op": "eq", "value": True},
                                    {"field": "disability_percent", "op": "gte", "value": 40}]})
    assert set(targets_of(s)) == {("is_bpl", "true"), ("disability", "yes")}


def test_any_block_alternatives_are_all_targets():
    s = scheme(eligibility={"all": [], "any": [{"field": "occupation", "op": "eq", "value": "farmer"},
                                               {"field": "occupation", "op": "eq", "value": "fisher"}]})
    assert set(targets_of(s)) == {("occupation", "farmer"), ("occupation", "fisher")}


def test_contradictory_age_rules_give_no_band():
    s = scheme(eligibility={"all": [{"field": "age", "op": "gte", "value": 60},
                                    {"field": "age", "op": "lte", "value": 18}]})
    assert [t for t in targets_of(s) if t[0] == "age_band"] == []


# ---------- categories, level, columns ----------

def test_categories_from_words_and_targets():
    s = scheme()
    assert "pension" in categories_of(s, targets_of(s))
    assert "women_child" in categories_of(s, targets_of(s))           # widowed target


def test_level_follows_scope():
    assert level_of(scheme(state=None)) == "central"
    assert level_of(scheme()) == "state"
    assert level_of(scheme(district="beed")) == "district"


def test_derive_builds_every_row_set():
    d = derive(scheme(name_hi="विधवा पेंशन"))
    assert d.level == "state" and d.columns["state_key"] == "maharashtra"
    assert len(d.rules) == 7 and d.rules[0]["block"] == "all" and d.rules[0]["source_quote"]
    assert ("विधवा पेंशन", "hi") in [(n, lang) for n, lang, _ in d.names]
    assert d.documents == [("Death certificate of husband", "death_cert")]
    assert d.sources[0]["is_primary"] and d.sources[0]["url"].startswith("https://")
    assert d.columns["annual_value_inr"] == 18000                     # Rs 1,500 a month
    assert d.columns["rule_count"] == 7 and d.columns["verified"] is False


def test_curated_scheme_defaults_to_verified():
    s = scheme(origin="curated")
    s.pop("verified")
    assert derive(s).columns["verified"] is True


# ---------- quality ----------

def test_full_record_scores_high_and_parts_add_up():
    q = quality_of(scheme(verified=True), today=date(2026, 11, 11))
    assert q.score == round(sum(q.parts.values()))
    assert q.score >= 90


def test_scheme_with_no_rules_scores_low():
    bare = scheme(eligibility={"all": []}, unmapped_criteria=["Women in difficult circumstances"],
                  documents=[], how_to_apply="", benefit_summary="Support for women")
    assert quality_of(bare, today=date(2026, 11, 11)).score <= 10


def test_scope_rules_earn_no_rule_points():
    only_scope = scheme(eligibility={"all": [{"field": "state", "op": "eq", "value": "maharashtra"}]})
    assert quality_of(only_scope).parts["rules"] == 0


def test_unmapped_conditions_lower_the_checkable_share():
    clean = quality_of(scheme()).parts["checkable"]
    vague = quality_of(scheme(unmapped_criteria=["a", "b", "c", "d", "e", "f"])).parts["checkable"]
    assert vague < clean == 20.0


def test_old_source_loses_freshness_points():
    assert quality_of(scheme(last_verified="2024-01-01"), today=date(2026, 11, 11)).parts["fresh"] == 0
    assert quality_of(scheme(last_verified="2026-10-23"), today=date(2026, 11, 11)).parts["fresh"] == 5


def test_every_stored_scheme_derives_without_error():
    """Real catalog (read-only): derive must accept every scheme the extractor has produced so far."""
    import json
    import sqlite3
    from pathlib import Path

    import pytest
    real = Path(__file__).resolve().parent.parent / "data" / "schemes.db"
    if not real.exists():
        pytest.skip("no data/schemes.db")
    conn = sqlite3.connect(f"file:{real.as_posix()}?mode=ro", uri=True)
    rows = [json.loads(r[0]) for r in conn.execute("SELECT data FROM schemes")]
    conn.close()
    assert rows
    for s in rows:
        d = derive(s)
        assert 0 <= d.columns["quality_score"] <= 100
        assert d.level in ("central", "state", "district")
