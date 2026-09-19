"""
Tests for src/fulltext.py — finding a free full-text copy of a paper.

Spec: docs/implementation_plan_2026-09-19_full_text.md#C2 (FT3)
No network: every finder's HTTP goes through an injected get_json, and every
download through an injected download().
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.fulltext import NoText, find_full_text, normalise_title, title_matches
from src.sources.errors import RateLimitedError, SourceUnavailableError

PAPER = {"title": "Maternal Stress and Infant Cortisol: A Cohort Study",
         "authors": "Smith J; Jones A", "pub_date": "2024-03-01",
         "doi": "", "canonical_id": "title:x"}


# ── Matching (adversarial first) ─────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Maternal Stress and Infant Cortisol: A Cohort Study Protocol",   # one word more
    "Maternal Stress and Infant Cortisol",                            # a prefix
    "Paternal Stress and Infant Cortisol: A Cohort Study",            # one word differs
])
def test_ft3_2_near_miss_title_is_rejected(title):
    """A similar title with the same author and year is still another paper."""
    assert title_matches(PAPER, title, "John Smith", 2024) is False


def test_ft3_3_title_alone_is_not_enough():
    """Same title, different first author and year: a different paper (e.g. a
    replication with the same name)."""
    assert title_matches(PAPER, PAPER["title"], "Maria Garcia", 2019) is False


@pytest.mark.parametrize("author,year,ok", [
    ("John Smith", 2019, True),        # author matches, either name order
    ("Smith, J.", None, True),
    ("Maria Garcia", "2024", True),    # year matches
    ("", None, False),
])
def test_ft3_3_title_plus_author_or_year(author, year, ok):
    assert title_matches(PAPER, "maternal stress & infant cortisol — a cohort study",
                         author, year) is ok


def test_ft3_3_unconfirmable_paper_is_refused():
    """No author and no year on our side: nothing to confirm a title hit with."""
    bare = {"title": PAPER["title"]}
    assert title_matches(bare, PAPER["title"], "John Smith", 2024) is False


def test_normalise_title():
    assert normalise_title("Stress & the  Brain: Café Études!") == "stress and the brain cafe etudes"


# ── The chain ────────────────────────────────────────────────────────────────

def _json(routes):
    """get_json stub: routes maps a URL substring to a reply (dict, None or an
    exception instance). Records calls."""
    calls = []

    def get(url, params):
        calls.append((url, dict(params)))
        for key, reply in routes.items():
            if key in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return None
    get.calls = calls
    return get


def _download(texts):
    """download stub: texts maps URL -> text, or -> NoText reason string."""
    got = []

    def download(url):
        got.append(url)
        value = texts.get(url, NoText("the link leads to a web page, not a PDF"))
        if isinstance(value, Exception):
            raise value
        return value
    download.got = got
    return download


def test_ft3_1_chain_order_and_stop():
    """Own link (a web page) → Unpaywall (none) → OpenAlex (found): stops there,
    Semantic Scholar is never asked."""
    paper = {**PAPER, "doi": "10.1/x"}
    get = _json({"unpaywall.org": {"best_oa_location": None, "oa_locations": []},
                 "openalex.org/works/doi:": {"best_oa_location": {"pdf_url": "https://repo/x.pdf"}},
                 "semanticscholar": {"openAccessPdf": {"url": "https://s2/x.pdf"}}})
    dl = _download({"https://repo/x.pdf": "Full text of the paper."})
    r = find_full_text(paper, dl, own_links=["https://doi.org/10.1/x"], email="me@x.org",
                       get_json=get)
    assert r.found and r.source == "OpenAlex" and r.url == "https://repo/x.pdf"
    assert dl.got == ["https://doi.org/10.1/x", "https://repo/x.pdf"]
    assert not any("semanticscholar" in url for url, _ in get.calls)
    assert r.tried == ["the paper's own link: the link leads to a web page, not a PDF",
                       "Unpaywall: no free copy", "OpenAlex: full text found"]


def test_ft3_1_title_hit_that_is_another_paper_is_not_downloaded():
    """End to end: OpenAlex's title search returns a near-miss; nothing is fetched."""
    get = _json({"openalex.org/works": {"results": [
        {"title": PAPER["title"] + " Protocol", "publication_year": 2024,
         "authorships": [{"author": {"display_name": "John Smith"}}],
         "best_oa_location": {"pdf_url": "https://wrong/paper.pdf"}}]}})
    dl = _download({"https://wrong/paper.pdf": "Some other paper."})
    r = find_full_text(PAPER, dl, own_links=[], get_json=get)
    assert not r.found and dl.got == []


def test_ft3_4_title_search_can_be_turned_off():
    """by_title=False: a paper with no DOI makes no title queries at all."""
    get = _json({})
    r = find_full_text(PAPER, _download({}), own_links=[], by_title=False, get_json=get)
    assert get.calls == [] and not r.found


def test_ft3_6_outage_is_not_absence():
    """A finder that could not be reached is 'unreachable', not 'no free copy' —
    and the chain carries on to the next one."""
    paper = {**PAPER, "doi": "10.1/x"}
    get = _json({"openalex.org": SourceUnavailableError("api.openalex.org returned 503"),
                 "semanticscholar": RateLimitedError("rate-limited")})
    r = find_full_text(paper, _download({}), own_links=[], email="me@x.org", get_json=get)
    assert r.unreachable == ["OpenAlex", "Semantic Scholar"]
    assert "OpenAlex: could not be reached" in r.tried
    assert "Unpaywall: no free copy" in r.tried


def test_ft3_unpaywall_is_skipped_and_said_without_an_email():
    r = find_full_text({**PAPER, "doi": "10.1/x"}, _download({}), own_links=[],
                       email="", get_json=_json({}))
    assert "Unpaywall: skipped (no contact email set)" in r.tried


def test_ft3_download_cap_is_announced():
    """P9: with more candidates than the cap, what was skipped is reported."""
    paper = {**PAPER, "doi": "10.1/x"}
    get = _json({"unpaywall.org": {"oa_locations": [{"url_for_pdf": f"https://u/{i}.pdf"}
                                                    for i in range(5)]}})
    dl = _download({})
    r = find_full_text(paper, dl, own_links=[], email="me@x.org", max_downloads=2,
                       get_json=get)
    assert len(dl.got) == 2
    assert any("limit of 2 PDFs reached" in t for t in r.tried)


def test_ft3_empty_pdf_moves_on():
    get = _json({"semanticscholar.org/graph/v1/paper/DOI:": {"openAccessPdf": {"url": "https://s/x.pdf"}}})
    dl = _download({"https://a/x.pdf": "", "https://s/x.pdf": "Real text."})
    r = find_full_text({**PAPER, "doi": "10.1/x"}, dl, own_links=["https://a/x.pdf"],
                       get_json=get)
    assert r.source == "Semantic Scholar"
    assert "the paper's own link: the PDF had no extractable text" in r.tried
