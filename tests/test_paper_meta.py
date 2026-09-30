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


def test_openalex_empty_when_not_found_or_no_index_raises_when_unreachable():
    """Since review M21 "not found" is "", but an outage raises, so abstract
    recovery lists OpenAlex as failed rather than as having nothing."""
    import requests as _rq
    from src.sources.errors import SourceUnavailableError
    with patch("src.paper_meta.requests.get", return_value=_status_resp(404)):
        assert fetch_openalex_abstract("10.1/x") == ""
    with patch("src.paper_meta.requests.get", return_value=_status_resp(200, {})):
        assert fetch_openalex_abstract("10.1/x") == ""
    with patch("src.paper_meta.requests.get", side_effect=_rq.ConnectionError("boom")), \
         patch("src.sources.base.time.sleep"):
        with pytest.raises(SourceUnavailableError):
            fetch_openalex_abstract("10.1/x")
    with patch("src.paper_meta.requests.get", return_value=_status_resp(403)):
        with pytest.raises(SourceUnavailableError):
            fetch_openalex_abstract("10.1/x")


def test_openalex_sets_a_timeout_and_identifies_itself(monkeypatch):
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "ops@example.org")
    with patch("src.paper_meta.requests.get",
               return_value=_json_resp({"abstract_inverted_index": {"a": [0]}})) as g:
        fetch_openalex_abstract("10.1/x")
    assert g.call_args.kwargs["timeout"] == paper_meta.OPENALEX_TIMEOUT
    assert g.call_args.kwargs["headers"]["User-Agent"] == "biorx/1.0 (mailto:ops@example.org)"


def test_openalex_contact_comes_from_the_environment(monkeypatch):
    """
    A contact address is the user's configuration, not a source literal.

    Batch H (backlog §6): with no address the header carries no mailto. The
    previous version asserted a placeholder address was sent instead, which
    told OpenAlex a contact existed when none did.
    """
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "ops@example.org")
    assert paper_meta.openalex_user_agent() == "biorx/1.0 (mailto:ops@example.org)"
    # Env var takes priority; removing it should yield no mailto even when
    # sources_config.yaml happens to have a personal email locally.
    monkeypatch.delenv("BIORX_CONTACT_EMAIL")
    monkeypatch.setattr("src.sources.config.load_sources_config", lambda: {})
    assert paper_meta.openalex_user_agent() == "biorx/1.0"


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


# ── M21: an outage is reported as an outage, not as "nothing found" ──────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M21
# The Europe PMC, PMC full-text and OpenAlex helpers caught every exception and
# returned None/"", so recover_abstract never listed them in `failed`, and the
# user was told the sources had nothing instead of "try again".

def _status_resp(code, payload=None):
    import requests as _rq
    m = MagicMock(spec=_rq.Response)
    m.status_code = code
    m.ok = code < 400
    m.json.return_value = payload or {}
    m.text = ""
    return m


def test_m21_outage_reported_as_failed():
    import requests as _rq
    from src.paper_meta import recover_abstract
    from src.sources.europepmc import EuropePmcAdapter

    epmc = EuropePmcAdapter()
    epmc.session.get = MagicMock(side_effect=_rq.ConnectionError("down"))
    with patch("src.paper_meta._europepmc", return_value=epmc), \
         patch("src.paper_meta._crossref_abstract", return_value=""), \
         patch("src.paper_meta.requests.get", return_value=_status_resp(503)), \
         patch("src.sources.base.time.sleep"), \
         patch("src.paper_meta.scrape_abstract_from_url", return_value=""):
        result = recover_abstract({"doi": "10.1/x", "pmcid": "PMC1"})
    assert not result.found
    assert {"europepmc", "pmc_fulltext", "openalex"} <= set(result.failed), result.failed


def test_m21_not_found_is_not_a_failure():
    """Adversarial: a 404 is a real "nothing there", not an outage."""
    from src.paper_meta import recover_abstract
    from src.sources.europepmc import EuropePmcAdapter

    epmc = EuropePmcAdapter()
    epmc.session.get = MagicMock(side_effect=lambda url, **kw: (
        _status_resp(404) if "fullTextXML" in url
        else _status_resp(200, {"resultList": {"result": []}})))
    with patch("src.paper_meta._europepmc", return_value=epmc), \
         patch("src.paper_meta._crossref_abstract", return_value=""), \
         patch("src.paper_meta.requests.get", return_value=_status_resp(404)), \
         patch("src.paper_meta.scrape_abstract_from_url", return_value=""):
        result = recover_abstract({"doi": "10.1/x", "pmcid": "PMC1"})
    assert not result.found
    assert result.failed == []


def test_m21_openalex_retried():
    idx = {"abstract_inverted_index": {"recovered": [0]}}
    with patch("src.paper_meta.requests.get",
               side_effect=[_status_resp(503), _status_resp(200, idx)]) as g, \
         patch("src.sources.base.time.sleep"):
        assert fetch_openalex_abstract("10.1/x") == "recovered"
    assert g.call_count == 2


# ── B8: hostile HTML cannot stall the server ─────────────────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B8

def test_b8_adversarial_html_is_linear():
    """5 MB of unclosed <p class="abstract"> tags. The old regexes re-scanned to
    the end of the page from every one of them (quadratic: hours)."""
    import time
    from src import paper_meta
    page = '<html><body>' + '<p class="abstract" ' * 250_000
    assert len(page) > 5_000_000
    started = time.monotonic()
    result = paper_meta._abstract_from_html(page)
    elapsed = time.monotonic() - started
    assert result == ""
    assert elapsed < 5, f"took {elapsed:.1f}s"


def test_b8_parser_finds_each_kind_of_abstract():
    from src import paper_meta
    body = "Background. " + "This study measured loneliness in adults. " * 4
    ld = '<script type="application/ld+json">{"@type": "ScholarlyArticle", "description": "%s"}</script>' % body
    assert paper_meta._abstract_from_html(ld) == body.strip()
    meta = '<meta name="citation_abstract" content="%s &amp; more">' % body
    assert paper_meta._abstract_from_html(meta).endswith("& more")
    div = '<section id="Abs1"><h2>Abstract</h2><p>%s</p></section><p>not this</p>' % body
    got = paper_meta._abstract_from_html(div)
    assert body.strip() in got and "not this" not in got
    assert paper_meta._abstract_from_html("<p>short</p>") == ""


@pytest.mark.parametrize("page", [
    '<p class="abstract" ' * 250_000,                  # unclosed tags
    '<script>x</script>' * 300_000,                    # many scripts
    '<div class="abstract">' + '<div>' * 400_000,      # deep nesting
], ids=["unclosed", "scripts", "nested"])
def test_b8_scanner_is_linear_without_the_cap(page):
    """The page cap is a second line; the scanner alone must stay linear.
    (html.parser took 67 s for 320 KB of unclosed tags on Python 3.12.3.)"""
    import time
    from src import paper_meta
    started = time.monotonic()
    for _ in paper_meta._scan_html(page):
        pass
    assert time.monotonic() - started < 5


def test_b8_scrape_cap_from_config_and_logged(caplog):
    """Gate 6 note 2: the cap comes from sources_config.yaml, and a cut page
    says so in the log instead of reading as "no abstract"."""
    import logging
    from src import paper_meta
    from src.sources.config import load_sources_config
    assert paper_meta.scrape_max_chars() == \
        load_sources_config()["full_text"]["scrape_max_chars"]
    page = ("<p>" + "x" * 5000 + "</p>"
            + '<div class="abstract">' + "Late abstract text. " * 8 + '</div>')
    with caplog.at_level(logging.INFO, logger="src.paper_meta"):
        assert paper_meta._abstract_from_html(page, max_chars=1000) == ""
    assert f"read the first 1000 of {len(page)} characters" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.paper_meta"):
        assert "Late abstract" in paper_meta._abstract_from_html(page, max_chars=len(page))
    assert "read the first" not in caplog.text


# ── T1.6: a landing page is not tried as the paper's own PDF ─────────────────

def test_t16_landing_page_is_not_tried_as_the_pdf():
    """Plan 2026-09-29 T1.6: a stored paper keeps Unpaywall's landing page in
    best_oa_url (a PMC article page) when it had no PDF link."""
    from src.paper_meta import summary_pdf_link
    pmc = {"doi": "10.1/x", "pmcid": "PMC123", "source": "europepmc",
           "best_oa_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123"}
    assert summary_pdf_link(pmc) == "https://doi.org/10.1/x"
    for url in ("https://europepmc.org/articles/PMC123/pdf/x.pdf",
                "https://academic.oup.com/j/article-pdf/1/2/3/x",
                "https://repo.example/bitstream/1/paper.PDF"):
        assert summary_pdf_link({**pmc, "best_oa_url": url}) == url
    assert summary_pdf_link({**pmc, "pdf_url": "https://p/x.pdf"}) == "https://p/x.pdf"
    # bioRxiv still gets its constructed PDF link.
    bx = {"doi": "10.1101/1", "source": "biorxiv_medrxiv", "version": "2",
          "best_oa_url": "https://www.biorxiv.org/content/10.1101/1v2"}
    assert summary_pdf_link(bx) == "https://www.biorxiv.org/content/10.1101/1v2.full.pdf"
