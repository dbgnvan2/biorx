"""Tests for unpaywall.py — OA enrichment adapter."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock

from src.sources.unpaywall import UnpaywallAdapter
from src.sources.schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
from src.sources.errors import SourceUnavailableError, RateLimitedError


def _make_record(doi="10.1234/test"):
    cid = make_canonical_id(doi=doi, title="Test", first_author="Smith", year=2024)
    return CanonicalRecord(
        canonical_id=cid, title="Test Paper", abstract="Abstract.",
        authors=[AuthorRecord(display_name="Smith J", sequence=1)],
        year=2024, published_date="2024-01-01", document_type="article",
        is_preprint=False, journal_or_server="Test Journal",
        doi=doi, pmid="", pmcid="",
        source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="", subjects=[], keywords=[],
        source_hits=[SourceHit(source="europepmc", source_record_id=doi, fetched_at="2024-01-01")],
        flags=RecordFlags(), source_trust_weight=1.0,
    )


UNPAYWALL_OA_FIXTURE = {
    "doi": "10.1234/test",
    "oa_status": "gold",
    "best_oa_location": {
        "url_for_pdf": "https://example.com/paper.pdf",
        "url": "https://example.com/paper",
        "license": "cc-by",
    },
}


# ── HTTP status handling ──────────────────────────────────────────────────────

def test_404_returns_none():
    adapter = UnpaywallAdapter(email="test@example.com")
    mock_resp = MagicMock(ok=False, status_code=404)
    with patch.object(adapter.session, "get", return_value=mock_resp):
        result = adapter.get_by_id("10.1234/missing")
    assert result is None


def test_422_returns_none():
    """422 = DOI not indexed by Unpaywall — should silently return None, not raise."""
    adapter = UnpaywallAdapter(email="test@example.com")
    mock_resp = MagicMock(ok=False, status_code=422)
    with patch.object(adapter.session, "get", return_value=mock_resp):
        result = adapter.get_by_id("10.20944/preprints202409.0624.v1")
    assert result is None


def test_422_does_not_raise():
    """Regression: 422 used to raise SourceUnavailableError and log a warning."""
    adapter = UnpaywallAdapter(email="test@example.com")
    mock_resp = MagicMock(ok=False, status_code=422)
    with patch.object(adapter.session, "get", return_value=mock_resp):
        try:
            adapter.get_by_id("10.1007/s12264-020-00575-7")
        except SourceUnavailableError:
            pytest.fail("422 should not raise SourceUnavailableError")


def test_429_raises_rate_limited():
    adapter = UnpaywallAdapter(email="test@example.com")
    mock_resp = MagicMock(ok=False, status_code=429)
    with patch.object(adapter.session, "get", return_value=mock_resp):
        with pytest.raises(RateLimitedError):
            adapter.get_by_id("10.1234/test")


def test_500_raises_source_unavailable():
    adapter = UnpaywallAdapter(email="test@example.com")
    mock_resp = MagicMock(ok=False, status_code=500)
    with patch.object(adapter.session, "get", return_value=mock_resp):
        with pytest.raises(SourceUnavailableError):
            adapter.get_by_id("10.1234/test")


def test_200_returns_data():
    adapter = UnpaywallAdapter(email="test@example.com")
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = UNPAYWALL_OA_FIXTURE
    with patch.object(adapter.session, "get", return_value=mock_resp):
        result = adapter.get_by_id("10.1234/test")
    assert result["oa_status"] == "gold"


# ── enrich() behaviour ────────────────────────────────────────────────────────

def test_enrich_populates_oa_fields():
    adapter = UnpaywallAdapter(email="test@example.com")
    record = _make_record()
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = UNPAYWALL_OA_FIXTURE
    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.enrich(record)
    assert record.oa_status == "gold"
    assert record.pdf_url == "https://example.com/paper.pdf"
    assert record.best_oa_url == "https://example.com/paper.pdf"
    assert record.license == "cc-by"
    assert record.flags.fulltext_reusable is True


def test_enrich_noop_on_missing_doi():
    adapter = UnpaywallAdapter(email="test@example.com")
    record = _make_record(doi="")
    record.doi = ""
    with patch.object(adapter.session, "get") as mock_get:
        adapter.enrich(record)
    mock_get.assert_not_called()


def test_enrich_noop_on_422(caplog):
    """422 during enrich must not change the record and must not log a WARNING."""
    import logging
    adapter = UnpaywallAdapter(email="test@example.com")
    record = _make_record()
    mock_resp = MagicMock(ok=False, status_code=422)
    with patch.object(adapter.session, "get", return_value=mock_resp):
        with caplog.at_level(logging.WARNING, logger="src.sources.unpaywall"):
            adapter.enrich(record)
    # Record should be unchanged
    assert record.oa_status == ""
    assert record.pdf_url == ""
    # No warnings should have been emitted for a 422
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 0


def test_enrich_does_not_overwrite_existing_oa_url():
    """If the record already has a best_oa_url, Unpaywall should not overwrite it."""
    adapter = UnpaywallAdapter(email="test@example.com")
    record = _make_record()
    record.best_oa_url = "https://existing-url.com/paper"
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = UNPAYWALL_OA_FIXTURE
    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.enrich(record)
    assert record.best_oa_url == "https://existing-url.com/paper"
