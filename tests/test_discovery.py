"""Scraping pipeline and conversation, fully offline: fake HTTP, fake search, mock LLM."""
from pathlib import Path

import httpx
import pytest

from core.eligibility import evaluate_scheme
from core.loader import load_schemes
from core.questions import next_best_question
from core.ranking import rank_schemes
from discovery.catalog import load_catalog, norm_name, save_discovered
from discovery.discover import Discoverer, DiscoveryReport
from discovery.search import FakeSearch, SearchResult
from ingest.extract import DROPPED_NOTE, build_scheme, quote_found
from ingest.fetch import FetchError, Fetcher, Page, html_to_text, is_blocked, is_official
from llm.providers.mock_provider import MockProvider

PENSION_HTML = """<html><head><title>Widow Pension Scheme - Social Justice Department</title></head><body>
<nav>Home | About</nav><h1>Widow Pension Scheme</h1>
<p>The applicant must be a resident of Maharashtra for at least 15 years.</p>
<p>The applicant should be a widow aged 40 years or above.</p>
<p>Annual family income should not exceed Rs. 21,000.</p>
<p>Benefit: Rs. 1,500 per month is paid by DBT into the bank account of the beneficiary.</p>
<p>Documents required: Death certificate of husband, Income certificate, Residence certificate.</p>
<script>var tracking = 1;</script></body></html>"""

ROBOTS_OK = "User-agent: *\nDisallow: /private/\n"


def make_fetcher(pages: dict[str, tuple[int, str, str]], tmp_path=None) -> Fetcher:
    """pages: url -> (status, content_type, body). robots.txt URLs included as needed."""
    def handler(request: httpx.Request):
        status, ctype, body = pages.get(str(request.url), (404, "text/plain", "not found"))
        return httpx.Response(status, headers={"content-type": ctype}, text=body)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return Fetcher(client=client, cache_dir=tmp_path, sleep=lambda s: None)


def page(text: str, url: str = "https://sjsa.maharashtra.gov.in/widow") -> Page:
    return Page(url=url, final_url=url, content_type="html", title="t", text=text,
                content_hash="abc123", fetched_at="2026-10-22T10:00:00+00:00")


PAGE_TEXT = html_to_text(PENSION_HTML)[1]


def extraction(**over):
    data = {
        "is_scheme_page": True, "name": "Widow Pension Scheme", "level": "state", "state": "Maharashtra",
        "benefit_summary": "Rs 1,500 per month", "benefit_annual_inr": 18000, "value_score": 4, "effort": 2,
        "all_rules": [
            {"field": "gender", "op": "eq", "value": "female", "reason": "widow",
             "source_quote": "The applicant should be a widow aged 40 years or above."},
            {"field": "marital_status", "op": "eq", "value": "widowed", "reason": "widow",
             "source_quote": "The applicant should be a widow aged 40 years or above."},
            {"field": "age", "op": "gte", "value": 40, "reason": "aged 40 or above",
             "source_quote": "a widow aged 40 years or above"},
            {"field": "annual_family_income_inr", "op": "lte", "value": 21000.0, "reason": "income limit",
             "source_quote": "Annual family income should not exceed Rs. 21,000."},
        ],
        "any_rules": [],
        "unmapped_criteria": [{"text": "Resident of Maharashtra for at least 15 years",
                               "source_quote": "resident of Maharashtra for at least 15 years"}],
        "documents": ["Death certificate of husband", "Income certificate"],
        "mentioned_schemes": [],
    }
    data.update(over)
    return data


# ---------- fetch ----------

def test_html_to_text_strips_scripts_and_nav():
    title, text = html_to_text(PENSION_HTML)
    assert title.startswith("Widow Pension Scheme")
    assert "tracking" not in text and "Home | About" not in text
    assert "Annual family income should not exceed Rs. 21,000." in text


def test_policy_blocks_myscheme_and_prefers_official():
    assert is_blocked("https://www.myscheme.gov.in/schemes/pm-kisan")
    assert is_blocked("https://india.gov.in/x")
    assert is_official("https://sjsa.maharashtra.gov.in/a") and is_official("https://agrimachinery.nic.in/")
    assert not is_official("https://www.example.com/scheme")


def test_fetch_honours_robots_and_caches(tmp_path):
    base = "https://sjsa.maharashtra.gov.in"
    f = make_fetcher({base + "/robots.txt": (200, "text/plain", ROBOTS_OK),
                      base + "/widow": (200, "text/html", PENSION_HTML),
                      base + "/private/x": (200, "text/html", PENSION_HTML)}, tmp_path)
    p = f.fetch(base + "/widow")
    assert p.content_type == "html" and "widow" in p.text.lower()
    with pytest.raises(FetchError, match="robots"):
        f.fetch(base + "/private/x")
    # second fetch comes from disk even if the site is now down
    f2 = make_fetcher({}, tmp_path)
    assert f2.fetch(base + "/widow").text == p.text


def test_fetch_refuses_blocked_unreadable_robots_and_thin_pages():
    f = make_fetcher({"https://a.gov.in/robots.txt": (403, "text/plain", "no"),
                      "https://b.gov.in/robots.txt": (404, "text/plain", ""),
                      "https://b.gov.in/app": (200, "text/html", "<html><body>Loading...</body></html>")})
    with pytest.raises(FetchError, match="blocked"):
        f.fetch("https://www.myscheme.gov.in/schemes/x")
    with pytest.raises(FetchError, match="robots"):
        f.fetch("https://a.gov.in/page")
    with pytest.raises(FetchError, match="too little text"):
        f.fetch("https://b.gov.in/app")


# ---------- extract ----------

def test_quote_check():
    norm = " ".join(PAGE_TEXT.lower().split())
    assert quote_found("Annual family income should not exceed Rs. 21,000.", norm)
    assert quote_found("annual  family income should not exceed Rs 21,000", norm)   # spacing/punctuation
    assert not quote_found("Applicant must own a tractor and two cows.", norm)
    assert not quote_found("widow", norm)                                          # too short to prove anything


def test_build_scheme_keeps_quoted_rules_with_defaults():
    res = build_scheme(extraction(), page(PAGE_TEXT), "mock")
    s = res.scheme
    assert s["origin"] == "discovered" and s["verified"] is False
    assert s["state"] == "maharashtra" and s["id"] == "widow_pension_scheme__maharashtra"
    assert res.kept_rules == 4
    income = [r for r in s["eligibility"]["all"] if r["field"] == "annual_family_income_inr"][0]
    assert income["value"] == 21000 and income["near_miss"] == 2100
    assert s["unmapped_criteria"] == ["Resident of Maharashtra for at least 15 years"]
    assert s["source_site"] == "sjsa.maharashtra.gov.in"


def test_invented_rule_is_dropped_and_scheme_flagged():
    data = extraction()
    data["all_rules"].append({"field": "owns_cultivable_land", "op": "eq", "value": False,
                              "source_quote": "Applicant must not own any agricultural land."})
    res = build_scheme(data, page(PAGE_TEXT), "mock")
    assert res.kept_rules == 4
    assert DROPPED_NOTE in res.scheme["unmapped_criteria"]
    assert any("quote not on page" in d for d in res.dropped)


def test_unknown_field_goes_to_unmapped_and_any_block_is_not_tightened():
    data = extraction(any_rules=[
        {"field": "age", "op": "gte", "value": 60, "source_quote": "a widow aged 40 years or above"},
        {"field": "has_tractor", "op": "eq", "value": True, "reason": "tractor owner",
         "source_quote": "The applicant should be a widow aged 40 years or above."}])
    res = build_scheme(data, page(PAGE_TEXT), "mock")
    assert "any" not in res.scheme["eligibility"]           # one alternative lost: drop the whole block
    assert "tractor owner" in res.scheme["unmapped_criteria"]


def test_listing_page_returns_mentions_only():
    res = build_scheme({"is_scheme_page": False, "mentioned_schemes": ["Lek Ladki Yojana"]}, page(PAGE_TEXT), "m")
    assert res.scheme is None and res.mentioned_schemes == ["Lek Ladki Yojana"]


# ---------- catalog ----------

def test_norm_name_matches_variants():
    assert norm_name("Pradhan Mantri Kisan Samman Nidhi Yojana") == norm_name("PM Kisan Samman Nidhi")


def test_save_discovered_new_unchanged_updated_and_curated_wins():
    s = build_scheme(extraction(), page(PAGE_TEXT), "mock").scheme
    assert save_discovered(s) == ("widow_pension_scheme__maharashtra", "new")
    assert save_discovered(s)[1] == "unchanged"
    assert save_discovered({**s, "content_hash": "changed"})[1] == "updated"
    curated_copy = {**s, "id": "x", "name": "PM-KISAN Samman Nidhi", "state": None}
    assert save_discovered(curated_copy) == ("pm_kisan", "curated_exists")
    cat = load_catalog()
    assert "widow_pension_scheme__maharashtra" in cat and "pm_kisan" in cat


def test_catalog_is_filtered_by_state_and_district():
    beed = _discovered(id="zp_beed", name="ZP Beed Grant", district="beed")
    save_discovered(beed)
    save_discovered(build_scheme(extraction(), page(PAGE_TEXT), "mock").scheme)   # maharashtra, no district
    assert set(load_catalog("maharashtra", "beed")) >= {"zp_beed", "pm_kisan"}
    assert "zp_beed" not in load_catalog("maharashtra", "pune")
    assert "zp_beed" not in load_catalog("maharashtra")          # no district given
    assert "widow_pension_scheme__maharashtra" not in load_catalog("kerala")


# ---------- discoverer ----------

def test_discoverer_search_fetch_extract_save():
    url = "https://sjsa.maharashtra.gov.in/widow"
    search = FakeSearch({"widow": [SearchResult("myscheme copy", "https://www.myscheme.gov.in/schemes/wp", ""),
                                   SearchResult("blog", "https://randomblog.com/widow", ""),
                                   SearchResult("Widow Pension", url, "")]})
    fetcher = make_fetcher({"https://sjsa.maharashtra.gov.in/robots.txt": (200, "text/plain", ROBOTS_OK),
                            url: (200, "text/html", PENSION_HTML)})
    llm = MockProvider(responses={"Page URL": extraction()})
    d = Discoverer(llm=llm, search=search, fetcher=fetcher)
    rep = d.ingest_queries(["widow pension maharashtra"])
    assert rep.new_schemes == ["widow_pension_scheme__maharashtra"]
    assert rep.pages_fetched == 1                       # myscheme and the blog were never requested
    # a second run does no work for the same URL
    rep2 = d.ingest_queries(["widow pension maharashtra"])
    assert rep2.pages_fetched == 0 and len(llm.calls) == 1


# ---------- engine with discovered schemes ----------

def _discovered(**over):
    s = build_scheme(extraction(unmapped_criteria=[]), page(PAGE_TEXT), "mock").scheme
    s.update(over)
    return s


def test_state_rule_and_statuses():
    s = _discovered()
    base = {"gender": "female", "marital_status": "widowed", "age": 50, "annual_family_income_inr": 20000}
    assert evaluate_scheme(s, {**base, "state": "maharashtra"}).status == "eligible_check_manually"  # extraction is unreviewed
    assert evaluate_scheme(s, {**base, "state": "kerala"}).status == "ineligible"
    assert evaluate_scheme(s, base).missing_fields == ["state"]
    r = evaluate_scheme({**s, "unmapped_criteria": ["Resident for 15 years"]}, {**base, "state": "maharashtra"})
    assert r.status == "eligible_check_manually" and r.verified is False and r.origin == "discovered"
    assert evaluate_scheme({**s, "eligibility": {"all": []}}, {"state": "maharashtra"}).status == "check_manually"


def test_ranking_prefers_verified_and_full_eligibility_on_ties():
    cat = dict(load_schemes())
    a = _discovered(id="a", rank={"value_score": 3, "effort": 2})
    b = _discovered(id="b", rank={"value_score": 3, "effort": 2}, unmapped_criteria=["check"])
    cat.update(a=a, b=b)
    prof = {"state": "maharashtra", "gender": "female", "marital_status": "widowed", "age": 50,
            "annual_family_income_inr": 20000}
    rk = rank_schemes([evaluate_scheme(cat[k], prof) for k in ("b", "a")], cat)
    assert [rk.start_with.scheme_id] + [r.scheme_id for r in rk.queue] == ["a", "b"]


def test_synthetic_question_for_field_without_curated_question():
    cat = {"x": _discovered(id="x", eligibility={"all": [
        {"field": "has_eshram_card", "op": "eq", "value": True, "reason": "e-Shram"}]})}
    q = next_best_question({"state": "maharashtra"}, cat)
    assert q["id"] == "field:has_eshram_card" and q["synthetic"]


# ---------- conversation ----------

class FakeDiscoverer:
    def __init__(self):
        self.profiles = []

    def discover_for_profile(self, profile, max_searches, max_pages):
        self.profiles.append(dict(profile))
        from discovery.discover import DiscoveryReport
        return DiscoveryReport(new_schemes=["widow_pension_scheme__maharashtra"])


def test_conversation_asks_searches_and_shortlists():
    from saathi.chat import Saathi
    first = {"language": "hi", "intent": "give_info", "facts": {
        "age": 65, "state": "maharashtra", "gender": "female", "occupation": "agricultural_labourer",
        "annual_family_income_inr": 20000, "social_category": "sc", "is_bpl": True,
        "owns_cultivable_land": False, "in_pmjay_beneficiary_list": False}}
    widowed = {"language": "hi", "intent": "give_info", "facts": {"marital_status": "widowed"}}
    skip = {"language": "hi", "intent": "dont_know", "facts": {}}
    extractor = MockProvider(queue=[first, widowed], responses={"": skip})
    agent = MockProvider(responses={"": "उत्तर"})
    disc = FakeDiscoverer()
    widow = _discovered(unmapped_criteria=[])
    bot = Saathi(extractor=extractor, agent=agent, discoverer=disc, out=lambda m: None,
                 catalog_loader=lambda: {**load_schemes(), widow["id"]: widow})
    bot.handle("मेरी उम्र 65 है, महाराष्ट्र, विधवा ...")
    assert disc.profiles and disc.profiles[0]["state"] == "maharashtra"   # searched once basics were known
    for _ in range(6):
        if bot.stage == "chat":
            break
        bot.handle("विधवा हूँ")
    assert bot.stage == "chat" and bot.lang == "hi"
    ids = [bot.shortlist["start_with"]["id"]] + [c["id"] for c in bot.shortlist["others"]]
    assert set(ids) == {"ignoaps", "widow_pension_scheme__maharashtra"}
    assert bot.shortlist["schemes_checked"] == 4
    # the agent only ever received engine JSON, never a raw page
    assert all("Page URL" not in str(c["messages"]) for c in agent.calls)


def test_conversation_new_fact_reruns_engine():
    from saathi.chat import Saathi
    facts = {"age": 65, "state": "maharashtra", "gender": "male", "occupation": "retired",
             "annual_family_income_inr": 20000, "social_category": "general", "is_bpl": False,
             "owns_cultivable_land": False, "in_pmjay_beneficiary_list": False}
    extractor = MockProvider(queue=[{"language": "en", "intent": "give_info", "facts": facts},
                                    {"language": "en", "intent": "give_info", "facts": {"is_bpl": True}}],
                             responses={"": {"language": "en", "intent": "dont_know", "facts": {}}})
    bot = Saathi(extractor=extractor, agent=MockProvider(responses={"": "ok"}), web=False,
                 out=lambda m: None, catalog_loader=load_schemes)
    bot.handle("I am 65, Maharashtra, retired man, income 20000, general, no BPL, no land, not in PM-JAY list")
    while bot.stage != "chat":
        bot.handle("don't know")
    assert bot.shortlist["start_with"] is None
    bot.handle("actually we do have a BPL card")
    assert bot.shortlist["start_with"]["id"] == "ignoaps"


def test_serpapi_no_results_is_empty_not_error():
    from discovery.search import SerpAPISearch

    def handler(request):
        return httpx.Response(200, json={"error": "Google hasn't returned any results for this query."})
    s = SerpAPISearch(api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)), cache_dir=None)
    assert s.search("very rare query") == []


def test_weak_evidence_is_check_manually_and_never_outranks_eligible():
    weak = _discovered(id="weak", rank={"value_score": 5, "effort": 1}, benefit_annual_inr=31000,
                       eligibility={"all": [{"field": "gender", "op": "eq", "value": "female", "reason": "girl"}]},
                       unmapped_criteria=["Single girl child", "Enrolled in first year of PG"])
    strong = _discovered(id="strong", rank={"value_score": 2, "effort": 4})
    prof = {"state": "maharashtra", "gender": "female", "marital_status": "widowed", "age": 50,
            "annual_family_income_inr": 20000}
    assert evaluate_scheme(weak, prof).status == "check_manually"
    from saathi.shortlist import build_shortlist
    sl = build_shortlist(prof, {"weak": weak, "strong": strong})
    assert sl["start_with"]["id"] == "strong"
    assert sl["check_on_official_page"][0]["name"] == weak["name"]


@pytest.mark.parametrize("text,expected", [
    ("Senior Citizen (age 60 years or above)", [("gte", 60)]),
    ("Applicants between 18 and 40 years of age", [("gte", 18), ("lte", 40)]),
    ("Age should be 65 years and above", [("gte", 65)]),
    ("Applicant must be below 40 years", [("lte", 40)]),
    ("Girls below 18 years", []),                         # about the child, not the applicant
    ("Student aged 18 to 25 years", []),
    ("Only for senior citizens", [("gte", 60)]),
    ("Must be a resident for 15 years", []),
])
def test_promote_unmapped_age(text, expected):
    from ingest.extract import promote_unmapped
    s = _discovered(unmapped_criteria=[text], eligibility={"all": [
        {"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    promote_unmapped(s)
    got = [(r["op"], r["value"]) for r in s["eligibility"]["all"] if r["field"] == "age"]
    assert got == expected
    assert (text in s["unmapped_criteria"]) == (not expected)


def test_markdown_is_stripped_from_replies():
    from saathi.explain import plain
    assert plain("## Title\n**Lek Ladki** - Rs 1,01,000") == "Title\nLek Ladki - Rs 1,01,000"


# ---------- phase 1: finding scheme URLs without the LLM ----------

LISTING_HTML = """<html><body><h1>Schemes</h1><ul>
<li><a href="/en/scheme/kanya-kalyan-yojana/">Kanya Kalyan Yojana</a></li>
<li><a href="/en/scheme/ill-health-assistance/">Illness Assistance</a></li>
<li><a href="/en/about/">About us</a></li>
<li><a href="https://other.gov.in/en/scheme/elsewhere/">Another site</a></li>
<li><a href="/en/schemes/page/2/">2</a></li></ul></body></html>"""
LISTING_PAGE2 = """<html><body><ul>
<li><a href="/en/scheme/third-scheme/">Third Scheme</a></li>
<li><a href="/en/schemes/">Back to page 1</a></li>
<li><a href="/en/about/">About us</a></li>
<li><a href="/en/contact/">Contact</a></li></ul></body></html>"""
SCHEME_PAGE = """<html><body><h1>Kanya Kalyan Yojana</h1>
<p>Eligibility: the applicant must be a permanent resident of Sangli district and the family
annual income should not exceed Rs. 1,00,000 as certified by the Tehsildar.</p>
<p>Benefit: financial assistance of Rs. 5,000 is paid into the bank account of the beneficiary
after the application is approved by the Zilla Parishad.</p>
<p>Documents required: birth certificate, income certificate, residence certificate, bank passbook.</p>
<p>How to apply: submit the form at the panchayat samiti office of your taluka.</p></body></html>"""

SITE = {"name": "ZP Sangli", "agency_type": "district", "district": "sangli",
        "start_url": "https://zpsangli.maharashtra.gov.in/", "host": "zpsangli.maharashtra.gov.in"}


def mh_fetcher(extra=None):
    base = "https://zpsangli.maharashtra.gov.in"
    pages = {f"{base}/robots.txt": (200, "text/plain", ROBOTS_OK),
             f"{base}/": (200, "text/html", LISTING_HTML),
             f"{base}/en/schemes/": (200, "text/html", LISTING_HTML),
             f"{base}/en/schemes/page/2/": (200, "text/html", LISTING_PAGE2),
             f"{base}/en/scheme/kanya-kalyan-yojana/": (200, "text/html", SCHEME_PAGE),
             f"{base}/en/scheme/ill-health-assistance/": (200, "text/html", SCHEME_PAGE),
             f"{base}/en/scheme/third-scheme/": (200, "text/html", SCHEME_PAGE)}
    pages.update(extra or {})
    return make_fetcher(pages)


def test_find_scheme_urls_follows_listings_and_pagination():
    from ingest.crawl import find_scheme_urls
    found = find_scheme_urls(SITE, mh_fetcher(), max_pages=10)
    names = sorted(u.rsplit("/en/scheme/")[-1] for u in found.scheme_urls)
    assert names == ["ill-health-assistance/", "kanya-kalyan-yojana/", "third-scheme/"]
    assert not any("other.gov.in" in u for u in found.scheme_urls)      # never leaves the site
    assert not any("/about/" in u for u in found.scheme_urls)


def test_prefilter_skips_pages_without_eligibility_wording():
    from ingest.crawl import worth_extracting
    assert worth_extracting(page(html_to_text(SCHEME_PAGE)[1]))
    assert not worth_extracting(page("Contact us. Phone: 020-1234. Address: ZP office, Sangli." * 6))
    assert not worth_extracting(page("short"))


def test_queue_is_remembered_and_not_re_queued():
    from store import db
    urls = [("https://a.gov.in/en/scheme/one/", "{}"), ("https://a.gov.in/en/scheme/two/", "{}")]
    assert db.queue_urls(urls) == 2
    assert db.queue_urls(urls) == 0                       # already waiting
    assert db.queued_count() == 2
    db.mark_url(urls[0][0], "scheme", "one")
    assert db.queued_count() == 1


def test_queued_url_is_not_treated_as_already_processed():
    from store import db
    d = Discoverer(llm=MockProvider(responses={"Page URL": extraction()}), search=FakeSearch(),
                   fetcher=mh_fetcher())
    url = "https://zpsangli.maharashtra.gov.in/en/scheme/kanya-kalyan-yojana/"
    db.queue_urls([(url, "{}")])
    assert d.process_url(url, DiscoveryReport()) is not None   # queued != done


def test_state_scheme_on_a_district_site_keeps_no_district():
    """A Zilla Parishad site also publishes state-wide schemes; those must stay visible statewide."""
    d = Discoverer(llm=MockProvider(responses={"Page URL": extraction(is_district_specific=False)}),
                   search=FakeSearch(), fetcher=mh_fetcher())
    sid = d.process_url("https://zpsangli.maharashtra.gov.in/en/scheme/kanya-kalyan-yojana/",
                        DiscoveryReport(), extra={"agency_type": "district", "agency_name": "ZP Sangli",
                                                  "district": "sangli"})
    from store import db
    s = db.get_scheme(sid)
    assert s["district"] is None and s["state"] == "maharashtra" and s["agency_type"] == "district"


def test_district_scheme_keeps_its_district():
    d = Discoverer(llm=MockProvider(responses={"Page URL": extraction(is_district_specific=True)}),
                   search=FakeSearch(), fetcher=mh_fetcher())
    sid = d.process_url("https://zpsangli.maharashtra.gov.in/en/scheme/kanya-kalyan-yojana/",
                        DiscoveryReport(), extra={"agency_type": "district", "agency_name": "ZP Sangli",
                                                  "district": "sangli"})
    from store import db
    assert db.get_scheme(sid)["district"] == "sangli"
