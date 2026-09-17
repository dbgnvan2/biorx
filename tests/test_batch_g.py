"""
Batch G test suite — fixes for test-qa findings (2026-09-16).

P5  — with_retry on sibling adapters
P28 — conftest DEFAULT_DATA_DIR fallback matches src.db
P27 — find_filter annotation is not X|Y union syntax (already fixed in test_batch_d)
P19 — failure detection round-trip through real orchestrator
P2  — monitor exits 2 on failed downloads
P3  — author-filter recall tradeoff documented
P28 (low-med) — normalize/to_dict coverage: biorxiv_medrxiv, socarxiv, schema
P10 — ensure_writable_directory creates a missing parent chain
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# ── P5: with_retry helper ──────────────────────────────────────────────────────

class TestWithRetry:
    """with_retry retries on transient failures and raises SourceUnavailableError."""

    def setup_method(self):
        from src.sources.base import with_retry
        from src.sources.errors import SourceUnavailableError
        self.with_retry = with_retry
        self.SourceUnavailableError = SourceUnavailableError

    def _ok_response(self, status=200):
        r = MagicMock(spec=requests.Response)
        r.status_code = status
        r.ok = status < 400
        return r

    def _err_response(self, status):
        r = MagicMock(spec=requests.Response)
        r.status_code = status
        r.ok = status < 400
        return r

    def test_g_retry_succeeds_on_first_try(self):
        resp = self._ok_response(200)
        fn = MagicMock(return_value=resp)
        result = self.with_retry(fn, source_label="X")
        assert result is resp
        assert fn.call_count == 1

    def test_g_retry_on_5xx_then_success(self):
        bad = self._err_response(503)
        good = self._ok_response(200)
        fn = MagicMock(side_effect=[bad, good])
        with patch("src.sources.base.time.sleep"):
            result = self.with_retry(fn, max_attempts=3, source_label="X")
        assert result is good
        assert fn.call_count == 2

    def test_g_retry_exhausted_raises_unavailable(self):
        bad = self._err_response(503)
        fn = MagicMock(return_value=bad)
        with patch("src.sources.base.time.sleep"):
            with pytest.raises(self.SourceUnavailableError, match="X"):
                self.with_retry(fn, max_attempts=3, source_label="X")
        assert fn.call_count == 3

    def test_g_retry_on_request_exception_then_success(self):
        exc = requests.ConnectionError("boom")
        good = self._ok_response(200)
        fn = MagicMock(side_effect=[exc, good])
        with patch("src.sources.base.time.sleep"):
            result = self.with_retry(fn, max_attempts=3, source_label="X")
        assert result is good

    def test_g_request_exception_exhausted_raises_unavailable(self):
        fn = MagicMock(side_effect=requests.ConnectionError("boom"))
        with patch("src.sources.base.time.sleep"):
            with pytest.raises(self.SourceUnavailableError):
                self.with_retry(fn, max_attempts=2, source_label="X")
        assert fn.call_count == 2

    def test_g_4xx_not_retried(self):
        """4xx responses are returned directly without retry."""
        r = self._err_response(404)
        fn = MagicMock(return_value=r)
        result = self.with_retry(fn, max_attempts=3, source_label="X")
        assert result is r
        assert fn.call_count == 1

    def test_g_http_error_4xx_not_retried(self):
        """requests.HTTPError with 4xx status is re-raised without retry."""
        r = self._err_response(404)
        exc = requests.HTTPError(response=r)
        fn = MagicMock(side_effect=exc)
        with pytest.raises(requests.HTTPError):
            self.with_retry(fn, max_attempts=3, source_label="X")
        assert fn.call_count == 1

    def test_g_backoff_doubles(self):
        """Retry sleeps 1s, 2s, ... (exponential backoff)."""
        bad = self._err_response(500)
        fn = MagicMock(return_value=bad)
        sleep_calls = []
        with patch("src.sources.base.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with pytest.raises(self.SourceUnavailableError):
                self.with_retry(fn, max_attempts=3, source_label="X")
        assert sleep_calls == [1, 2]


# ── P5: sibling adapters import with_retry ────────────────────────────────────

def test_g_europepmc_imports_with_retry():
    from src.sources import europepmc
    assert hasattr(europepmc, "with_retry")


def test_g_psyarxiv_imports_with_retry():
    from src.sources import psyarxiv
    assert hasattr(psyarxiv, "with_retry")


def test_g_socarxiv_imports_with_retry():
    from src.sources import socarxiv
    assert hasattr(socarxiv, "with_retry")


def test_g_crossref_imports_with_retry():
    from src.sources import crossref
    assert hasattr(crossref, "with_retry")


def test_g_unpaywall_imports_with_retry():
    from src.sources import unpaywall
    assert hasattr(unpaywall, "with_retry")


def test_g_biorxiv_medrxiv_imports_with_retry():
    from src.sources import biorxiv_medrxiv
    assert hasattr(biorxiv_medrxiv, "with_retry")


def test_g_europepmc_retries_on_5xx(monkeypatch):
    """EuropePMC search retries on a 5xx before succeeding."""
    from src.sources.europepmc import EuropePmcAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"resultList": {"result": []}, "hitCount": "0"}

    adapter = EuropePmcAdapter()
    monkeypatch.setattr(adapter.session, "get", MagicMock(side_effect=[bad, good]))
    with patch("src.sources.base.time.sleep"):
        result = adapter.search("test", page=1, page_size=10)
    assert result == []


# ── P28: conftest DEFAULT_DATA_DIR matches src.db ─────────────────────────────

def test_g_conftest_fallback_matches_db_constant():
    """conftest.DEFAULT_DATA_DIR must equal src.db.DEFAULT_DATA_DIR.

    The conftest has a try/except fallback for pytester subprocess sessions
    where src is not importable. That fallback must stay in sync with the real
    constant, or the artifact guard watches the wrong directory.
    """
    from src.db import DEFAULT_DATA_DIR as db_constant
    import tests.conftest as conftest_module
    assert conftest_module.DEFAULT_DATA_DIR == db_constant, (
        f"conftest fallback {conftest_module.DEFAULT_DATA_DIR!r} != "
        f"src.db.DEFAULT_DATA_DIR {db_constant!r} — update the fallback"
    )


# ── P19: failure detection round-trip via real orchestrator ───────────────────

def test_g_failure_detection_round_trip():
    """Source failure → orchestrator on_status → monitor detects FAILURE_STATUS_MARKER.

    If orchestrator.py's status string is reworded away from '— skipped',
    monitor.py's FAILURE_STATUS_MARKER check silently stops working while
    test_monitor.py stays green (it hardcodes the string). This test exercises
    the real on_status emission path end-to-end.
    """
    from src.sources.errors import SourceUnavailableError
    from src.sources.orchestrator import SourceOrchestrator
    from agents.monitor import FAILURE_STATUS_MARKER

    class _AlwaysFails:
        source_name = "europepmc"
        source_trust_weight = 1.0

        def search(self, *a, **kw):
            raise SourceUnavailableError("injected failure")

        def normalize(self, raw):
            raise NotImplementedError

        def get_by_id(self, identifier):
            return None

    config: dict = {}
    orch = SourceOrchestrator(config)
    # Inject the failing adapter directly — bypasses _register_adapters
    orch._search_adapters["europepmc"] = _AlwaysFails()

    status_messages: list = []
    orch.search(
        filter_dict={"text_groups": [{"both": "test"}], "days_back": 7},
        source_selection={"all": False, "selected": ["europepmc"]},
        on_status=status_messages.append,
    )

    failure_msgs = [m for m in status_messages if FAILURE_STATUS_MARKER in m]
    assert failure_msgs, (
        f"Expected at least one status message containing {FAILURE_STATUS_MARKER!r}; "
        f"got messages: {status_messages!r}. "
        "If orchestrator.py's wording changed, update FAILURE_STATUS_MARKER in monitor.py."
    )


# ── P2: monitor exits 2 on PDF download failure ───────────────────────────────

def test_g_monitor_exits_2_on_failed_downloads(tmp_path, monkeypatch):
    """main() returns 2 when any PDF download fails, not only on source failure."""
    import agents.monitor as mon

    fake_record = {
        "canonical_id": "doi:10.1234/x",
        "title": "X",
        "abstract": "",
        "authors": "",
        "published_date": "2024-01-01",
        "pdf_url": "https://example.com/x.pdf",
        "doi": "10.1234/x",
        "source": "europepmc",
        "flags": {"retracted": False, "corrected": False, "fulltext_reusable": False},
    }

    monkeypatch.setattr(mon, "load_filters", lambda path: [
        {"name": "t", "enabled": True, "terms": [["x"]], "operator": "AND"}
    ])
    monkeypatch.setattr(mon, "run_search", lambda *a, **kw: [fake_record])
    monkeypatch.setattr(mon, "filter_papers", lambda records, f: records)
    monkeypatch.setattr(mon, "download_pdf", lambda rec, dest: False)  # always fails

    download_dir = tmp_path / "pdfs"
    download_dir.mkdir()

    # Pass args directly — no sys.argv manipulation needed
    exit_code = mon.main([
        "--all",
        "--download-dir", str(download_dir),
    ])

    assert exit_code == 2, (
        f"Expected exit code 2 when downloads fail; got {exit_code}"
    )


# ── P3: author-filter recall tradeoff documented ──────────────────────────────

def test_g_author_filter_no_au_clause_in_arxiv_query():
    """
    Author filtering is client-side: the arXiv query contains no au: clause.
    A paper by the target author ranked beyond max_results is silently missed.
    This test documents the known tradeoff (P3 — confirmed, not fixed).
    If an au: clause is intentionally added later, update this assertion.
    """
    from src.sources.query_builder import build_arxiv_query
    filter_dict: dict = {
        "text_groups": [{"both": "neural network"}],
        "author": "LeCun",
        "days_back": 30,
    }
    query = build_arxiv_query(filter_dict)
    assert "au:" not in query, (
        "arXiv query now includes an au: clause — "
        "update test_g_author_filter_no_au_clause_in_arxiv_query if intentional"
    )
    assert "LeCun" not in query


# ── P28 (low-med): normalize and to_dict coverage ────────────────────────────

def test_g_biorxiv_normalize_roundtrip():
    """BiorxivMedrxivAdapter.normalize produces a valid CanonicalRecord."""
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter
    adapter = BiorxivMedrxivAdapter.__new__(BiorxivMedrxivAdapter)
    raw = {
        "doi": "10.1101/2024.01.15.575123",
        "version": "2",
        "title": "Test Paper",
        "abstract": "An abstract.",
        "authors": "Smith, J; Doe, A",
        "pub_date": "2024-01-15",
        "category": "neuroscience",
        "server": "biorxiv",
        "license": "cc_by",
    }
    rec = adapter.normalize(raw)
    d = rec.to_dict()
    assert d["doi"] == "10.1101/2024.01.15.575123"
    assert d["title"] == "Test Paper"
    assert "Smith" in d["authors"]
    assert "Doe" in d["authors"]
    assert d["is_preprint"] is True
    assert d["category"] == "neuroscience"
    # biorxiv_medrxiv uses its own version field, not the arxiv_version slot;
    # to_dict() version falls back to "1" when arxiv_version is unset
    assert d["version"] == "1"
    assert rec.year == 2024


def test_g_socarxiv_normalize_roundtrip():
    """SocArxivAdapter.normalize produces a valid CanonicalRecord."""
    from src.sources.socarxiv import SocArxivAdapter
    adapter = SocArxivAdapter.__new__(SocArxivAdapter)
    raw = {
        "id": "abc12",
        "attributes": {
            "title": "Social Science Paper",
            "description": "Abstract text.",
            "doi": "10.31235/osf.io/abc12",
            "date_created": "2024-02-10T00:00:00Z",
            "tags": ["sociology", "methods"],
            "subjects": [],
            "license": {"name": "CC-BY 4.0"},
        },
        "links": {"html": "https://osf.io/preprints/socarxiv/abc12/", "pdf": ""},
        "embeds": {"contributors": {"data": []}},
    }
    rec = adapter.normalize(raw)
    d = rec.to_dict()
    assert d["doi"] == "10.31235/osf.io/abc12"
    assert d["title"] == "Social Science Paper"
    assert d["abstract"] == "Abstract text."
    assert d["is_preprint"] is True
    assert rec.year == 2024
    assert "sociology" in rec.keywords


def test_g_schema_to_dict_roundtrip():
    """CanonicalRecord.to_dict() includes all fields _filter_papers() needs."""
    from src.sources.schema import (
        CanonicalRecord, AuthorRecord, SourceHit, RecordFlags
    )
    rec = CanonicalRecord(
        canonical_id="doi:10.1234/test",
        title="Test",
        abstract="Abstract.",
        authors=[
            AuthorRecord(display_name="Jane Doe", sequence=1),
            AuthorRecord(display_name="Bob Smith", sequence=2),
        ],
        year=2024,
        published_date="2024-01-01",
        document_type="preprint",
        is_preprint=True,
        journal_or_server="bioRxiv",
        doi="10.1234/test",
        pmid="", pmcid="",
        source_url="https://example.com",
        best_oa_url="https://example.com/pdf",
        pdf_url="https://example.com/pdf",
        license="cc-by",
        oa_status="open",
        subjects=["biology", "genetics"],
        keywords=["CRISPR"],
        source_hits=[SourceHit(
            source="biorxiv",
            source_record_id="10.1234/test",
            fetched_at="2024-01-01T00:00:00Z",
        )],
        flags=RecordFlags(fulltext_reusable=True),
    )
    d = rec.to_dict()

    assert d["title"] == "Test"
    assert d["doi"] == "10.1234/test"
    assert d["authors"] == "Jane Doe; Bob Smith"
    assert d["author_corresponding"] == "Jane Doe"
    assert d["is_preprint"] is True
    assert d["flags"]["fulltext_reusable"] is True
    assert d["source"] == "biorxiv"
    assert d["category"] == "biology"
    assert d["version"] == "1"     # arxiv_version default
    assert d["published"] == "NA"  # is_preprint=True → "NA"


def test_g_schema_to_dict_no_authors():
    """to_dict handles a record with no authors without raising."""
    from src.sources.schema import (
        CanonicalRecord, SourceHit, RecordFlags
    )
    rec = CanonicalRecord(
        canonical_id="title:abc123",
        title="No Authors",
        abstract="",
        authors=[],
        year=0,
        published_date="",
        document_type="preprint",
        is_preprint=True,
        journal_or_server="",
        doi="", pmid="", pmcid="",
        source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="",
        subjects=[], keywords=[],
        source_hits=[SourceHit(
            source="biorxiv",
            source_record_id="",
            fetched_at="2024-01-01T00:00:00Z",
        )],
        flags=RecordFlags(),
    )
    d = rec.to_dict()
    assert d["authors"] == ""
    assert d["author_corresponding"] == ""


# ── P10: ensure_writable_directory creates a missing parent chain ─────────────

def test_g_ensure_writable_directory_nested(tmp_path):
    """ensure_writable_directory creates a missing parent chain (parents=True)."""
    from src.db import ensure_writable_directory
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()
    ensure_writable_directory(nested)
    assert nested.is_dir()


def test_g_ensure_writable_directory_existing(tmp_path):
    """ensure_writable_directory is a no-op when the directory already exists."""
    from src.db import ensure_writable_directory
    d = tmp_path / "existing"
    d.mkdir()
    (d / "marker.txt").write_text("keep")
    ensure_writable_directory(d)
    assert (d / "marker.txt").exists()
