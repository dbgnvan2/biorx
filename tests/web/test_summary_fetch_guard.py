"""
POST /api/summaries fetches from URLs in the client's paper dict.

Found by the second security pass of the 2026-09-17 /csdp review: the summary
job downloaded `pdf_url(paper)` and scraped abstract URLs with plain requests
(any scheme, redirects followed, no address check), so an internal page could
be read back through a summary. The PDF also landed in the shared cache under
the client's DOI/title, where every later summary of the real paper read it.

SUM1 the PDF goes through src/safe_fetch; SUM2 nothing is written to the
shared cache; SUM3 abstract scraping on the web path goes through safe_fetch.
No network: every URL here is refused before any connection.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import safe_fetch
from src.paper_meta import recover_abstract


def _explode(*a, **k):
    raise AssertionError("an unguarded fetch was made")


@pytest.mark.parametrize("url", [
    "http://10.0.0.5/admin",                 # http, internal
    "https://127.0.0.1/secret",              # https, loopback literal
    "https://169.254.169.254/latest/meta-data/",
])
def test_sum1_internal_pdf_url_is_never_fetched(ctx, url):
    from web.routes_summaries import _extract_text
    with patch("src.pdf_handler.PDFHandler.download_pdf", _explode), \
         patch("src.pdf_handler.requests.get", _explode):
        assert _extract_text(ctx, {"title": "x", "pdf_url": url}) == ""


def test_sum2_pdf_text_is_read_without_touching_the_shared_cache(ctx, tmp_path):
    from src.pdf_handler import default_pdf_dir
    from web.routes_summaries import _extract_text
    cache = Path(default_pdf_dir())
    before = set(cache.rglob("*")) if cache.exists() else set()
    with patch.object(safe_fetch, "fetch_pdf", return_value=b"%PDF-1.7 fake"), \
         patch("src.pdf_handler.PDFHandler.extract_text", return_value="FULL TEXT"), \
         patch("src.pdf_handler.PDFHandler.download_pdf", _explode):
        text = _extract_text(ctx, {"title": "Real Paper", "doi": "10.1/real",
                                   "pdf_url": "https://pub.example/x.pdf"})
    assert text == "FULL TEXT"
    after = set(cache.rglob("*")) if cache.exists() else set()
    assert after == before, "the summary route wrote into the shared PDF cache"


def test_sum3_web_abstract_scrape_refuses_internal_urls():
    paper = {"title": "x", "source_url": "https://127.0.0.1/admin",
             "url": "http://10.0.0.5/"}
    with patch("src.paper_meta.requests.get", _explode):
        result = recover_abstract(paper, fetch_html=safe_fetch.fetch_html)
    assert not result.found


def test_sum3_summary_job_passes_the_guarded_fetcher(ctx, signed_in, monkeypatch):
    """The route must hand recover_abstract the guarded fetcher."""
    import time
    from unittest.mock import MagicMock
    from src.paper_meta import AbstractRecovery
    seen = {}

    def fake_recover(paper, fetch_html=None):
        seen["fetch_html"] = fetch_html
        return AbstractRecovery(tried=["stubbed"])

    monkeypatch.setattr("web.routes_summaries.recover_abstract", fake_recover)
    monkeypatch.setattr("web.routes_summaries._extract_text", lambda ctx, p: "")
    with patch("src.llm_providers.build_client", return_value=MagicMock()):
        r = signed_in.post("/api/summaries", json={
            "paper": {"title": "No abstract", "doi": "10.1/none"},
            "api_key": "sk-inline", "provider": "anthropic"})
        assert r.status_code == 202, r.text
        for _ in range(100):
            if "fetch_html" in seen:
                break
            time.sleep(0.02)
    assert seen.get("fetch_html") is safe_fetch.fetch_html
