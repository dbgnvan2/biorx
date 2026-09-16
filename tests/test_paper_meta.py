"""
Tests for src/paper_meta.py — PDF/landing-page URL resolution and abstract recovery.

Spec: docs/implementation_plan_2026-09-15.md#2.1 (Phase 0 extraction)
All network calls are mocked; no live HTTP.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import paper_meta
from src.paper_meta import (
    fetch_openalex_abstract, paper_link, pdf_url, scrape_abstract_from_url,
)


# ── URL resolution ────────────────────────────────────────────────────────────

def test_pdf_url_prefers_explicit_pdf_url():
    assert pdf_url({"pdf_url": "https://arxiv.org/pdf/2301.1v1",
                    "best_oa_url": "https://other", "doi": "10.1/x"}) \
        == "https://arxiv.org/pdf/2301.1v1"


def test_pdf_url_falls_back_to_best_oa_url():
    assert pdf_url({"best_oa_url": "https://oa.example/x.pdf", "doi": "10.1/x"}) \
        == "https://oa.example/x.pdf"


@pytest.mark.parametrize("source,server", [
    ("biorxiv_medrxiv", "biorxiv"), ("biorxiv", "biorxiv"), ("medrxiv", "medrxiv"),
])
def test_pdf_url_builds_the_preprint_server_url(source, server):
    got = pdf_url({"doi": "10.1101/abc", "version": "2", "source": source})
    assert got == f"https://www.{server}.org/content/10.1101/abcv2.full.pdf"


def test_pdf_url_falls_back_to_doi_resolver_then_source_url():
    assert pdf_url({"doi": "10.1/x"}) == "https://doi.org/10.1/x"
    assert pdf_url({"source_url": "https://pub.example/a"}) == "https://pub.example/a"
    assert pdf_url({}) == ""


def test_paper_link_prefers_the_landing_page_over_the_pdf():
    paper = {"source_url": "https://pub.example/article",
             "pdf_url": "https://pub.example/article.pdf", "doi": "10.1/x"}
    assert paper_link(paper) == "https://pub.example/article"


def test_paper_link_falls_back_to_doi_then_pdf():
    assert paper_link({"doi": "10.1/x"}) == "https://doi.org/10.1/x"
    assert paper_link({"pdf_url": "https://p/x.pdf"}) == "https://p/x.pdf"


# ── Abstract scraping ─────────────────────────────────────────────────────────

def _html_resp(body, ok=True):
    m = MagicMock()
    m.ok = ok
    m.text = body
    return m


ABSTRACT = ("We examine how adolescent peer networks buffer physiological stress "
            "responses across a two-year longitudinal cohort, with implications for "
            "social baseline theory and its predictions about regulatory economy.")


def test_scrape_reads_json_ld_description():
    body = ('<html><head><script type="application/ld+json">'
            '{"@type":"ScholarlyArticle","description":"%s"}</script></head>'
            '<body>%s</body></html>' % (ABSTRACT, "x" * 600))
    with patch("src.paper_meta.requests.get", return_value=_html_resp(body)):
        assert scrape_abstract_from_url("https://pub.example/a") == ABSTRACT


def test_scrape_reads_json_ld_nested_under_main_entity():
    """Springer/BMC wrap the article in WebPage > mainEntity."""
    body = ('<html><script type="application/ld+json">'
            '{"@type":"WebPage","mainEntity":{"description":"%s"}}</script>'
            '<body>%s</body></html>' % (ABSTRACT, "x" * 600))
    with patch("src.paper_meta.requests.get", return_value=_html_resp(body)):
        assert scrape_abstract_from_url("https://pub.example/a") == ABSTRACT


def test_scrape_falls_back_to_meta_description():
    body = '<html><head><meta name="description" content="%s"></head><body>%s</body></html>' % (
        ABSTRACT, "x" * 600)
    with patch("src.paper_meta.requests.get", return_value=_html_resp(body)):
        assert scrape_abstract_from_url("https://pub.example/a") == ABSTRACT


def test_scrape_falls_back_to_an_abstract_section():
    body = '<html><body><section id="abstract"><p>%s</p></section>%s</body></html>' % (
        ABSTRACT, "x" * 600)
    with patch("src.paper_meta.requests.get", return_value=_html_resp(body)):
        assert ABSTRACT.split()[0] in scrape_abstract_from_url("https://pub.example/a")


def test_scrape_rejects_a_short_site_tagline():
    """A 'description' shorter than MIN_ABSTRACT_CHARS is a tagline, not an abstract."""
    body = '<html><head><meta name="description" content="A journal."></head><body>%s</body></html>' % ("x" * 600)
    with patch("src.paper_meta.requests.get", return_value=_html_resp(body)):
        assert scrape_abstract_from_url("https://pub.example/a") == ""


def test_scrape_returns_empty_on_error_status_short_body_or_exception():
    with patch("src.paper_meta.requests.get", return_value=_html_resp("x" * 600, ok=False)):
        assert scrape_abstract_from_url("https://pub.example/a") == ""
    with patch("src.paper_meta.requests.get", return_value=_html_resp("tiny")):
        assert scrape_abstract_from_url("https://pub.example/a") == ""
    with patch("src.paper_meta.requests.get", side_effect=OSError("boom")):
        assert scrape_abstract_from_url("https://pub.example/a") == ""


def test_scrape_sets_a_timeout():
    """external-api E1: every request carries an explicit timeout."""
    with patch("src.paper_meta.requests.get", return_value=_html_resp("x" * 600)) as g:
        scrape_abstract_from_url("https://pub.example/a")
    assert g.call_args.kwargs["timeout"] == paper_meta.SCRAPE_TIMEOUT


# ── OpenAlex inverted index ───────────────────────────────────────────────────

def _json_resp(payload, ok=True):
    m = MagicMock()
    m.ok = ok
    m.json.return_value = payload
    return m


def test_openalex_reconstructs_word_order_from_the_inverted_index():
    idx = {"Social": [0], "baseline": [1], "theory": [2, 5], "predicts": [3],
           "regulatory": [4]}
    resp = _json_resp({"abstract_inverted_index": idx})
    with patch("src.paper_meta.requests.get", return_value=resp):
        assert fetch_openalex_abstract("10.1/x") == \
            "Social baseline theory predicts regulatory theory"


def test_openalex_strips_a_doi_url_prefix():
    with patch("src.paper_meta.requests.get",
               return_value=_json_resp({"abstract_inverted_index": {"a": [0]}})) as g:
        fetch_openalex_abstract("https://doi.org/10.1/x")
    assert g.call_args.args[0].endswith("works/doi:10.1/x")


def test_openalex_returns_empty_on_error_missing_index_or_exception():
    with patch("src.paper_meta.requests.get", return_value=_json_resp({}, ok=False)):
        assert fetch_openalex_abstract("10.1/x") == ""
    with patch("src.paper_meta.requests.get", return_value=_json_resp({})):
        assert fetch_openalex_abstract("10.1/x") == ""
    with patch("src.paper_meta.requests.get", side_effect=OSError("boom")):
        assert fetch_openalex_abstract("10.1/x") == ""


def test_openalex_sets_a_timeout_and_identifies_itself():
    with patch("src.paper_meta.requests.get",
               return_value=_json_resp({"abstract_inverted_index": {"a": [0]}})) as g:
        fetch_openalex_abstract("10.1/x")
    assert g.call_args.kwargs["timeout"] == paper_meta.OPENALEX_TIMEOUT
    assert "mailto:" in g.call_args.kwargs["headers"]["User-Agent"]


def test_openalex_contact_comes_from_the_environment(monkeypatch):
    """A contact address is deployment config, not a source literal."""
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "ops@example.org")
    assert paper_meta.openalex_user_agent() == "ResearchTool/1.0 (mailto:ops@example.org)"
    monkeypatch.delenv("BIORX_CONTACT_EMAIL")
    assert paper_meta.DEFAULT_CONTACT_EMAIL in paper_meta.openalex_user_agent()


def test_no_personal_email_is_hardcoded_in_this_module():
    source = Path(paper_meta.__file__).read_text()
    assert "@me.com" not in source


# ── The GUI must use these, not a private copy ────────────────────────────────

def test_gui_uses_the_shared_helpers():
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    assert gui._pdf_url is pdf_url
    assert gui._paper_link is paper_link
    assert gui._scrape_abstract_from_url is scrape_abstract_from_url
    assert gui._fetch_openalex_abstract is fetch_openalex_abstract
