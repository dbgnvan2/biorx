"""
Contract tests for source adapters.
Uses unittest.mock to avoid real network calls.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock


# ── Europe PMC adapter ────────────────────────────────────────────────────────

EPMC_FIXTURE = {
    "id": "12345678",
    "source": "MED",
    "pmid": "12345678",
    "pmcid": "PMC9876543",
    "doi": "10.1234/test.paper",
    "title": "Maternal stress and cortisol in adolescents",
    "authorString": "Smith J, Jones A",
    "authorList": {"author": [
        {"fullName": "John Smith", "sequence": "first",
         "authorId": {"type": "ORCID", "value": "0000-0001-2345-6789"}},
        {"fullName": "Alice Jones", "sequence": "additional"},
    ]},
    "journalTitle": "Journal of Stress Research",
    "pubYear": "2024",
    "firstPublicationDate": "2024-03-15",
    "abstractText": "This study examines maternal stress and its effects...",
    "isOpenAccess": "Y",
    "license": "CC BY",
    "pubType": "journal article",
    "isPreprint": "N",
    "keywordList": {"keyword": ["stress", "cortisol", "adolescents"]},
}

def test_europepmc_normalize_produces_canonical():
    from src.sources.europepmc import EuropePmcAdapter
    adapter = EuropePmcAdapter()
    record = adapter.normalize(EPMC_FIXTURE)

    assert record.title == "Maternal stress and cortisol in adolescents"
    assert record.doi == "10.1234/test.paper"
    assert record.pmid == "12345678"
    assert record.pmcid == "PMC9876543"
    assert record.abstract == "This study examines maternal stress and its effects..."
    assert record.published_date == "2024-03-15"
    assert record.year == 2024
    assert record.document_type == "article"
    assert record.is_preprint is False
    assert record.license == "CC BY"
    assert record.oa_status == "open"
    assert len(record.authors) == 2
    assert record.authors[0].display_name == "John Smith"
    assert record.authors[0].orcid == "0000-0001-2345-6789"
    assert record.source_hits[0].source == "europepmc"
    assert record.canonical_id.startswith("doi:")


def test_europepmc_normalize_preprint():
    fixture = dict(EPMC_FIXTURE, pubType="preprint", isPreprint="Y",
                   doi="10.1101/preprint.test", pmcid="")
    from src.sources.europepmc import EuropePmcAdapter
    record = EuropePmcAdapter().normalize(fixture)
    assert record.is_preprint is True
    assert record.document_type == "preprint"


def test_europepmc_search_calls_api():
    from src.sources.europepmc import EuropePmcAdapter
    adapter = EuropePmcAdapter()

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "hitCount": 1,
        "nextCursorMark": "ABC",
        "resultList": {"result": [EPMC_FIXTURE]},
    }

    with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
        results = adapter.search("maternal AND FIRST_PDATE:[2025-01-01 TO 2025-03-29]")

    mock_get.assert_called_once()
    assert len(results) == 1
    assert results[0]["doi"] == "10.1234/test.paper"


def test_e2_2_europepmc_exposes_last_total():
    """E2.2: search() surfaces the API hitCount as adapter.last_total for progress."""
    from src.sources.europepmc import EuropePmcAdapter
    adapter = EuropePmcAdapter()
    assert adapter.last_total is None  # nothing fetched yet

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "hitCount": 999,
        "nextCursorMark": "ABC",
        "resultList": {"result": [EPMC_FIXTURE]},
    }

    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.search("maternal AND FIRST_PDATE:[2025-01-01 TO 2025-03-29]")

    assert adapter.last_total == 999


# ── PsyArXiv adapter ──────────────────────────────────────────────────────────

PSYARXIV_FIXTURE = {
    "id": "abc12",
    "type": "preprints",
    "attributes": {
        "title": "Attachment and emotion regulation in adolescents",
        "description": "This preprint examines attachment theory...",
        "date_created": "2025-03-20T10:00:00Z",
        "doi": "10.31234/osf.io/abc12",
        "tags": ["attachment", "emotion", "adolescents"],
        "subjects": [[{"id": "6012", "text": "Social and Behavioral Sciences"}]],
        "license": {"name": "CC-By Attribution 4.0 International"},
    },
    "links": {
        "html": "https://osf.io/preprints/psyarxiv/abc12/",
        "pdf":  "https://osf.io/abc12/download",
    },
    "embeds": {},
}

def test_psyarxiv_normalize_produces_canonical():
    from src.sources.psyarxiv import PsyArxivAdapter
    adapter = PsyArxivAdapter()
    record  = adapter.normalize(PSYARXIV_FIXTURE)

    assert record.title == "Attachment and emotion regulation in adolescents"
    assert record.doi   == "10.31234/osf.io/abc12"
    assert record.abstract == "This preprint examines attachment theory..."
    assert record.is_preprint is True
    assert record.document_type == "preprint"
    assert record.journal_or_server == "PsyArXiv"
    assert record.oa_status == "open"
    assert record.published_date == "2025-03-20"
    assert record.year == 2025
    assert record.source_hits[0].source == "psyarxiv"
    assert record.pdf_url == "https://osf.io/abc12/download"
    assert "attachment" in record.keywords


def test_psyarxiv_search_calls_osf_api():
    from src.sources.psyarxiv import PsyArxivAdapter
    adapter = PsyArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [PSYARXIV_FIXTURE],
        "meta": {"total": 1},
    }

    with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
        results = adapter.search("attachment", filter_dict={"days_back": 7})

    mock_get.assert_called_once()
    assert len(results) == 1


# ── Crossref adapter ──────────────────────────────────────────────────────────

CROSSREF_FIXTURE = {
    "title": ["Maternal stress in adolescents: a longitudinal study"],
    "abstract": "Extended abstract from Crossref...",
    "author": [
        {"given": "John", "family": "Smith", "ORCID": "http://orcid.org/0000-0001-2345-6789"},
    ],
    "published": {"date-parts": [[2024, 3, 15]]},
    "container-title": ["Journal of Stress Research"],
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
}

def test_crossref_enrich_fills_missing_fields():
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags, make_canonical_id

    adapter = CrossrefAdapter()
    record = CanonicalRecord(
        canonical_id="doi:10.1234/test", title="", abstract="",
        authors=[], year=0, published_date="", document_type="article",
        is_preprint=False, journal_or_server="", doi="10.1234/test",
        pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="", subjects=[], keywords=[],
        source_hits=[], flags=RecordFlags(),
    )

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": CROSSREF_FIXTURE}

    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.enrich(record)

    assert record.title == "Maternal stress in adolescents: a longitudinal study"
    assert record.published_date == "2024-03-15"
    assert record.journal_or_server == "Journal of Stress Research"
    assert len(record.authors) == 1
    assert record.authors[0].display_name == "John Smith"


def test_crossref_strips_jats_xml_from_abstract():
    """Crossref abstracts often contain JATS markup — it must be stripped."""
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags, make_canonical_id

    jats_abstract = (
        "<jats:p>Background: Some background.</jats:p>"
        "<jats:p>Methods: The method.</jats:p>"
        "<jats:p>Results: Findings here.</jats:p>"
    )
    fixture = dict(CROSSREF_FIXTURE, abstract=jats_abstract)

    adapter = CrossrefAdapter()
    cid = make_canonical_id(doi="10.1234/test", title="", first_author="", year=0)
    record = CanonicalRecord(
        canonical_id=cid, title="", abstract="",
        authors=[], year=0, published_date="", document_type="article",
        is_preprint=False, journal_or_server="", doi="10.1234/test",
        pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="", subjects=[], keywords=[],
        source_hits=[], flags=RecordFlags(),
    )

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": fixture}

    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.enrich(record)

    assert "<jats:" not in record.abstract
    assert "Background:" in record.abstract
    assert "Methods:" in record.abstract
    assert "Results:" in record.abstract


def test_crossref_skips_when_no_doi():
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags

    adapter = CrossrefAdapter()
    record = CanonicalRecord(
        canonical_id="title:abc", title="No DOI Paper", abstract="", authors=[],
        year=0, published_date="", document_type="other", is_preprint=False,
        journal_or_server="", doi="", pmid="", pmcid="", source_url="",
        best_oa_url="", pdf_url="", license="", oa_status="", subjects=[],
        keywords=[], source_hits=[], flags=RecordFlags(),
    )

    with patch.object(adapter.session, "get") as mock_get:
        adapter.enrich(record)

    mock_get.assert_not_called()  # no DOI → no Crossref call


def test_i3_crossref_enrich_reports_a_failed_lookup():
    """Issue 3: enrich returns False when Crossref could not be asked, so the
    orchestrator can count an outage; True for a DOI Crossref does not know."""
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags

    def record():
        return CanonicalRecord(
            canonical_id="doi:10.1234/test", title="", abstract="",
            authors=[], year=0, published_date="", document_type="article",
            is_preprint=False, journal_or_server="", doi="10.1234/test",
            pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="",
            license="", oa_status="", subjects=[], keywords=[],
            source_hits=[], flags=RecordFlags(),
        )

    adapter = CrossrefAdapter()
    with patch.object(adapter.session, "get", return_value=MagicMock(ok=False, status_code=503)), \
         patch("time.sleep"):
        assert adapter.enrich(record()) is False
    with patch.object(adapter.session, "get", return_value=MagicMock(ok=False, status_code=404)):
        assert adapter.enrich(record()) is True


# ── B3: OSF sources see every term, or no title filter at all ────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B3
# Before the fix only query.split()[0] was sent as filter[title]: for
# "Loneliness" every "social isolation" preprint was dropped by the server.

def _osf_adapter(pages_by_title=None, cfg=None):
    """PsyArXiv adapter whose session.get answers from pages_by_title[term]."""
    from unittest.mock import MagicMock
    from src.sources.psyarxiv import PsyArxivAdapter

    adapter = PsyArxivAdapter(sources_config=cfg or {})
    pages_by_title = pages_by_title or {}

    def get(url, params=None, timeout=None):
        term = params.get("filter[title]")
        data = pages_by_title.get(term, [])
        resp = MagicMock(status_code=200, ok=True)
        resp.json.return_value = {"data": data, "meta": {"total": len(data)}}
        return resp

    adapter.session.get = MagicMock(side_effect=get)
    return adapter


def _sent_titles(adapter):
    return [c.kwargs["params"].get("filter[title]") for c in adapter.session.get.call_args_list]


def _two_group_filter():
    return {"days_back": 7, "start_date": "", "end_date": "",
            "text_groups": [
                {"title": "loneliness", "abstract": "family", "both": ""},
                {"title": "social Isolation", "abstract": "family", "both": ""},
            ]}


def test_b3_every_or_group_reaches_osf():
    adapter = _osf_adapter({"loneliness": [{"id": "a"}],
                            "social Isolation": [{"id": "b"}, {"id": "a"}]})
    out = adapter.search("ignored", filter_dict=_two_group_filter())

    assert _sent_titles(adapter) == ["loneliness", "social Isolation"]
    assert [r["id"] for r in out] == ["a", "b"]          # merged, no repeat
    assert adapter.last_page_size == 3                   # what the source sent


def test_b3_multiword_term_sent_whole():
    adapter = _osf_adapter()
    adapter.search("maternal deprivation", filter_dict={
        "days_back": 7, "text_groups": [{"title": "Maternal deprivation*"}]})
    assert _sent_titles(adapter) == ["Maternal deprivation"]


def test_prog1_osf_reports_each_term_request():
    adapter = _osf_adapter()
    seen = []
    adapter.on_activity = seen.append
    adapter.search("x", filter_dict={"days_back": 7, "text_groups": [
        {"title": "stress"}, {"title": "cortisol"}]})
    assert seen == ["page 1, searching title word 1 of 2…",
                    "page 1, searching title word 2 of 2…"]


def test_b3_group_without_title_term_fetches_by_date():
    """A group matching on abstract only could be lost by a title filter."""
    adapter = _osf_adapter()
    adapter.search("x", filter_dict={"days_back": 7, "text_groups": [
        {"title": "stress"}, {"abstract": "cortisol"}]})
    assert _sent_titles(adapter) == [None]


def test_b3_terms_over_cap_fall_back_to_date_only():
    groups = [{"title": f"term{i}"} for i in range(4)]
    adapter = _osf_adapter(cfg={"osf": {"max_title_terms": 3}})
    adapter.search("x", filter_dict={"days_back": 7, "text_groups": groups})
    assert _sent_titles(adapter) == [None]


def test_b3_exhausted_term_not_requested_again():
    full = [{"id": f"x{i}"} for i in range(50)]
    adapter = _osf_adapter({"a": full, "b": [{"id": "y"}]})
    fd = {"days_back": 7, "text_groups": [{"title": "a"}, {"title": "b"}]}
    adapter.search("q", page=1, page_size=50, filter_dict=fd)
    adapter.session.get.reset_mock()
    adapter.search("q", page=2, page_size=50, filter_dict=fd)
    assert _sent_titles(adapter) == ["a"]     # "b" ended on page 1


# ── B7 / M18: bioRxiv and medRxiv, over the filter's own dates ───────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B7, #M18
# Before the fix the adapter called search_recent(days=days_back, server="biorxiv"):
# a saved date range was ignored (days_back 0 meant "today only") and medRxiv
# was never asked.

def _biorxiv_adapter(collections, cfg=None):
    """collections[(server, cursor)] -> list of raw papers the API returns."""
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter
    adapter = BiorxivMedrxivAdapter(sources_config=cfg or {})
    calls = []

    def by_range(start_date, end_date, category=None, server="biorxiv", cursor=0, **_):
        calls.append((server, start_date, end_date, cursor))
        coll = collections.get((server, cursor), [])
        total = sum(len(v) for (s, _c), v in collections.items() if s == server)
        return {"messages": [{"total": total}],
                "collection": [dict(p, server=server) for p in coll]}

    adapter._api.search_by_date_range = by_range
    adapter._api.search_recent = MagicMock(side_effect=AssertionError("search_recent used"))
    return adapter, calls


def test_b7_date_range_used():
    # A short recent range (BW: a range ending long ago is no longer read
    # directly, so the 2020–2026-06 range this test used to send is now
    # test_bw3_old_range_makes_no_requests).
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=10)
    adapter, calls = _biorxiv_adapter({})
    adapter.search("x", filter_dict={"days_back": 0, "start_date": start.isoformat(),
                                     "end_date": end.isoformat()})
    assert calls and all(c[1:3] == (start.isoformat(), end.isoformat()) for c in calls)


def test_b7_medrxiv_queried():
    adapter, calls = _biorxiv_adapter({
        ("biorxiv", 0): [{"doi": "10.1101/b1", "title": "B", "version": "1"}],
        ("medrxiv", 0): [{"doi": "10.1101/m1", "title": "M", "version": "1"}],
    })
    out = adapter.search("x", filter_dict={"days_back": 7})
    assert {c[0] for c in calls} == {"biorxiv", "medrxiv"}
    recs = [adapter.normalize(r) for r in out]
    assert {r.journal_or_server for r in recs} == {"biorxiv", "medrxiv"}
    assert any(r.source_url.startswith("https://www.medrxiv.org/") for r in recs)


def test_b7_servers_configurable():
    # The real config shape (TD6: the old test used a top-level key, the same
    # wrong shape the adapter read, so the bug was not caught).
    adapter, calls = _biorxiv_adapter(
        {}, cfg={"publication_sources": {"biorxiv_medrxiv": {"servers": ["medrxiv"]}}})
    adapter.search("x", filter_dict={"days_back": 7})
    assert {c[0] for c in calls} == {"medrxiv"}


def test_b7_pages_through_each_server():
    full = [{"doi": f"10.1101/b{i}", "title": f"B{i}", "version": "1"} for i in range(100)]
    adapter, calls = _biorxiv_adapter({("biorxiv", 0): full,
                                       ("biorxiv", 100): full[:5],
                                       ("medrxiv", 0): full[:3]})
    fd = {"days_back": 7}
    adapter.search("x", page=1, filter_dict=fd)
    adapter.search("x", page=2, filter_dict=fd)
    assert ("biorxiv", 100) in [(c[0], c[3]) for c in calls]
    # medRxiv's short first page ended it: not asked again on page 2.
    assert [(c[0], c[3]) for c in calls].count(("medrxiv", 100)) == 0


def test_m18_biorxiv_version_carried():
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter
    from src.filtering import filter_papers
    rec = BiorxivMedrxivAdapter.__new__(BiorxivMedrxivAdapter).normalize({
        "doi": "10.1101/x", "title": "Revised", "version": "2", "server": "biorxiv"})
    d = rec.to_dict()
    assert d["version"] == "2"
    assert filter_papers([d], {"version": "2+ (revised only)"}) == [d]
    assert filter_papers([d], {"version": "1 (first submission only)"}) == []


# ── BW2/BW3: read directly only where it helps ───────────────────────────────
# docs/implementation_plan_2026-10-07_biorxiv_window.md

BW_CFG = {"publication_sources": {"biorxiv_medrxiv": {"max_direct_days": 21,
                                                      "europepmc_lag_days": 60}}}


def test_bw2_long_range_reads_only_the_newest_days():
    from datetime import date
    adapter, calls = _biorxiv_adapter({}, cfg=BW_CFG)
    with patch("src.sources.biorxiv_medrxiv._today", return_value=date(2026, 10, 7)):
        adapter.search("x", filter_dict={"days_back": 0, "start_date": "2019-01-01",
                                         "end_date": "2026-10-07"})
    assert calls and all(c[1:3] == ("2026-09-17", "2026-10-07") for c in calls)


def test_bw3_old_range_makes_no_requests():
    """Real-scale (P9): 2019–2020 is ~300k papers; it used to read 150 pages
    per server from January 2019 and stop. Now: no request at all."""
    from datetime import date
    adapter, calls = _biorxiv_adapter({("biorxiv", 0): [{"doi": "10.1101/x", "title": "x"}]},
                                      cfg=BW_CFG)
    with patch("src.sources.biorxiv_medrxiv._today", return_value=date(2026, 10, 7)):
        out = adapter.search("x", filter_dict={"days_back": 0, "start_date": "2019-01-01",
                                               "end_date": "2020-12-31"})
    assert calls == [] and list(out) == []
    assert adapter.has_more is False and adapter.last_total == 0


# ── M19: an empty container-title is not a Crossref outage ───────────────────

def test_m19_empty_container_title():
    """Crossref returns "container-title": [] for many posted-content DOIs.
    Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M19"""
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags

    adapter = CrossrefAdapter()
    adapter.get_by_id = MagicMock(return_value={
        "container-title": [], "author": [{"given": "Ann", "family": "Lee"}]})
    rec = CanonicalRecord(
        canonical_id="doi:10.1/x", title="T", abstract="", authors=[], year=2024,
        published_date="2024-01-01", document_type="preprint", is_preprint=True,
        journal_or_server="", doi="10.1/x", pmid="", pmcid="", source_url="",
        best_oa_url="", pdf_url="", license="", oa_status="", subjects=[],
        keywords=[], source_hits=[], flags=RecordFlags())
    assert adapter.enrich(rec) is True
    assert rec.journal_or_server == ""
    assert [a.display_name for a in rec.authors] == ["Ann Lee"]   # backfill still ran


# ── B5 (found live): Europe PMC types come from pubTypeList ──────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B5
# tests/fixtures/europepmc_core_sample.json is trimmed from real core-format
# responses (2026-09-28). The adapter read a `pubType` field the API does not
# send, so every record was "other" and no Europe PMC preprint was flagged.

def _epmc_sample():
    import json
    path = Path(__file__).parent / "fixtures" / "europepmc_core_sample.json"
    return json.loads(path.read_text())


def test_b5_europepmc_types_from_real_response():
    from src.sources.europepmc import EuropePmcAdapter
    a = EuropePmcAdapter()
    got = {tuple(r["pubTypeList"]["pubType"]): a.normalize(r) for r in _epmc_sample()}
    assert got[("Journal Article",)].document_type == "article"
    assert got[("Review", "Journal Article")].document_type == "review"
    assert got[("Editorial",)].document_type == "other"
    pre = got[("Preprint",)]
    assert pre.document_type == "preprint" and pre.is_preprint
    assert pre.to_dict()["published"] == "NA"


def test_b5_real_review_matches_review_filter():
    """Adversarial: the saved "Loneliness" filter matched 0 of 709 live."""
    from src.sources.europepmc import EuropePmcAdapter
    from src.filtering import filter_papers
    papers = [EuropePmcAdapter().normalize(r).to_dict() for r in _epmc_sample()]
    reviews = filter_papers(papers, {"paper_type": "review article"})
    assert [p["type"] for p in reviews] == ["review"]


def test_b5_trial_type_mapped():
    from src.sources.europepmc import EuropePmcAdapter
    r = EuropePmcAdapter().normalize({"title": "t", "pubTypeList": {
        "pubType": ["Randomized Controlled Trial", "Journal Article"]}})
    assert r.document_type == "trial"


def test_b5_real_licences_read():
    from src.sources.europepmc import EuropePmcAdapter
    from src import filter_vocabulary as vocab
    ids = {vocab.license_id(EuropePmcAdapter().normalize(r).license) for r in _epmc_sample()}
    assert {"cc-by", "cc0", "cc-by-nc-nd", "cc-by-nd"} <= ids


def _api_with_pages(pools, page=30, title=None, doi=None):
    """A stand-in for the bioRxiv details API as it answers today: `page`
    papers per call from a pool per server, with the pool size as `total`.
    title(server, i) gives each paper's title (default "<server> <i>");
    doi(server, i) its DOI (default unique per paper)."""
    title = title or (lambda server, i: f"{server} {i}")
    doi = doi or (lambda server, i: f"10.1101/{server}{i}")
    colls = {}
    for server, n in pools.items():
        papers = [{"doi": doi(server, i), "title": title(server, i), "version": "1"}
                  for i in range(n)]
        for c in range(0, n, page):
            colls[(server, c)] = papers[c:c + page]
    adapter, calls = _biorxiv_adapter(colls)
    return adapter, calls


def test_b7_reads_past_a_30_paper_page():
    """Browser run 2026-09-29: the API sends 30 papers per call. Assuming 100
    ended every server after its first page, so a search read 60 of ~4,900.
    Real scale for the cap: pools several pages deep on both servers."""
    adapter, calls = _api_with_pages({"biorxiv": 95, "medrxiv": 40})
    fd = {"days_back": 7}
    got, page = [], 1
    while True:
        got += adapter.search("x", page=page, filter_dict=fd)
        if not adapter.has_more:
            break
        page += 1
    assert len(got) == 135 and len({p["doi"] for p in got}) == 135
    assert [c[3] for c in calls if c[0] == "biorxiv"] == [0, 30, 60, 90]
    assert [c[3] for c in calls if c[0] == "medrxiv"] == [0, 30]
    assert adapter.last_total == 135


def test_br9_pubmed_label_without_a_journal():
    from src.sources.pubmed import PubMedAdapter
    a = PubMedAdapter()
    assert a.normalize({"title": "T", "pmid": "1"}).journal_or_server == "PubMed"
    rec = a.normalize({"title": "T", "pmid": "1", "journalTitle": "Lancet"})
    assert rec.journal_or_server == "Lancet (PubMed)"


def test_td7_window_kept_across_pages():
    """Midnight between page 1 and page 2 must not move the window."""
    from datetime import date
    full = [{"doi": f"10.1101/b{i}", "title": f"B{i}", "version": "1"} for i in range(30)]
    adapter, calls = _biorxiv_adapter({("biorxiv", 0): full, ("biorxiv", 30): full[:5]},
                                      cfg=BW_CFG)
    fd = {"days_back": 0, "start_date": "2026-01-01", "end_date": "2026-10-07"}
    with patch("src.sources.biorxiv_medrxiv._today", return_value=date(2026, 10, 7)):
        adapter.search("x", page=1, filter_dict=fd)
    with patch("src.sources.biorxiv_medrxiv._today", return_value=date(2026, 12, 7)):
        adapter.search("x", page=2, filter_dict=fd)     # 61 days later: would be SKIP
    biorxiv = [c for c in calls if c[0] == "biorxiv"]
    assert [c[3] for c in biorxiv] == [0, 30]
    assert {c[1:3] for c in biorxiv} == {("2026-09-17", "2026-10-07")}


def test_td6_servers_read_from_the_shipped_config_shape():
    from src.sources.config import load_sources_config
    cfg = load_sources_config()
    cfg["publication_sources"]["biorxiv_medrxiv"]["servers"] = ["biorxiv"]
    adapter, calls = _biorxiv_adapter({}, cfg=cfg)
    adapter.search("x", filter_dict={"days_back": 7})
    assert {c[0] for c in calls} == {"biorxiv"}
