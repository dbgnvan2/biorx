"""
Batch H test suite — mutation-killing tests from test-qa findings (2026-09-16).

P·mutation — with_retry (base.py): pin default max_attempts, backoff schedule,
  and 4xx/5xx split at the status-500 boundary.
P·mutation — orchestrator pagination: page advances on each loop iteration.
P·mutation — adapter search() retry: all sibling adapters retry on 5xx.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── with_retry: default max_attempts = 3 ──────────────────────────────────────

def test_h_with_retry_default_max_attempts():
    """with_retry default max_attempts is 3 when not specified by caller."""
    from src.sources.base import with_retry
    from src.sources.errors import SourceUnavailableError

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False
    fn = MagicMock(return_value=bad)

    with patch("src.sources.base.time.sleep"):
        with pytest.raises(SourceUnavailableError):
            with_retry(fn, source_label="X")  # no max_attempts — uses default

    assert fn.call_count == 3, (
        f"default max_attempts must be 3; fn was called {fn.call_count} time(s)"
    )


# ── with_retry: backoff schedule (2^0=1s, 2^1=2s) ────────────────────────────

def test_h_with_retry_backoff_schedule_on_5xx_response():
    """Bare 5xx responses sleep 1s then 2s before the final attempt."""
    from src.sources.base import with_retry
    from src.sources.errors import SourceUnavailableError

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False
    fn = MagicMock(return_value=bad)

    sleep_calls = []
    with patch("src.sources.base.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(SourceUnavailableError):
            with_retry(fn, max_attempts=3, source_label="X")

    assert sleep_calls == [1, 2], (
        f"expected backoff [1, 2] (2^0, 2^1); got {sleep_calls}"
    )


def test_h_with_retry_backoff_schedule_on_request_exception():
    """requests.RequestException path also sleeps 1s then 2s."""
    from src.sources.base import with_retry
    from src.sources.errors import SourceUnavailableError

    fn = MagicMock(side_effect=requests.ConnectionError("boom"))

    sleep_calls = []
    with patch("src.sources.base.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(SourceUnavailableError):
            with_retry(fn, max_attempts=3, source_label="X")

    assert sleep_calls == [1, 2], (
        f"expected backoff [1, 2] (2^0, 2^1) on RequestException; got {sleep_calls}"
    )


# ── with_retry: 4xx/5xx split boundary at status 500 ─────────────────────────

def test_h_with_retry_499_not_retried():
    """Status 499 is a 4xx — returned directly, no retry."""
    from src.sources.base import with_retry

    r = MagicMock(spec=requests.Response)
    r.status_code = 499
    r.ok = False
    fn = MagicMock(return_value=r)

    result = with_retry(fn, max_attempts=3, source_label="X")
    assert result is r
    assert fn.call_count == 1, "4xx must not be retried"


def test_h_with_retry_500_is_retried():
    """Status 500 is the first 5xx — triggers retry logic."""
    from src.sources.base import with_retry
    from src.sources.errors import SourceUnavailableError

    r = MagicMock(spec=requests.Response)
    r.status_code = 500
    r.ok = False
    fn = MagicMock(return_value=r)

    with patch("src.sources.base.time.sleep"):
        with pytest.raises(SourceUnavailableError):
            with_retry(fn, max_attempts=3, source_label="X")

    assert fn.call_count == 3, "5xx must exhaust all attempts"


# ── orchestrator: page advances on every loop iteration ──────────────────────

def test_h_orchestrator_page_advances_to_2():
    """
    _search_source must increment page from 1 to 2 when the first page is full.

    If `page += 1` is mutated to `page += 0`, the second call still uses page=1
    and this assertion fails. The adapter is set up so it terminates on page 2
    (empty result), preventing a hang regardless of the mutation's direction.
    """
    from src.sources.orchestrator import SourceOrchestrator
    from src.sources.dedup import Deduplicator

    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = {}

    page_size = orch.PAGE_SIZE
    pages_seen: list[int] = []

    def _search(query, page=1, **kw):
        pages_seen.append(page)
        if page == 1:
            return [{"doi": f"10.1234/{i}", "title": f"P{i}"} for i in range(page_size)]
        return []  # page 2 → empty → terminates

    adapter = MagicMock()
    adapter.source_name = "europepmc"
    adapter.source_trust_weight = 1.0
    adapter.last_total = 0
    # None → orchestrator falls back to len(raw_records), avoiding MagicMock comparison
    adapter.last_page_size = None
    adapter.search.side_effect = _search
    adapter.normalize.side_effect = lambda raw: MagicMock(
        canonical_id=raw["doi"],
        doi=raw["doi"],
        pmid="",
        pmcid="",
        title=raw["title"],
        source_hits=[],
        source_trust_weight=1.0,
    )

    orch._search_source(
        "europepmc", adapter, "q", {}, Deduplicator(),
        None, None, None, max_results=10_000,
    )

    assert adapter.search.call_count >= 2, "source must be queried at least twice"
    assert pages_seen[0] == 1, f"first call must use page=1, got {pages_seen[0]}"
    assert pages_seen[1] == 2, (
        f"second call must use page=2 (page +=1), got {pages_seen[1]}. "
        "If this is 1, page is not advancing."
    )


# ── adapter search() retry on 5xx ─────────────────────────────────────────────

def test_h_psyarxiv_retries_on_5xx(monkeypatch):
    """PsyArXiv search retries on a 5xx before succeeding."""
    from src.sources.psyarxiv import PsyArxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"data": [], "meta": {"total": 0}}

    adapter = PsyArxivAdapter()
    monkeypatch.setattr(
        adapter.session, "get", MagicMock(side_effect=[bad, good])
    )
    with patch("src.sources.base.time.sleep"):
        result = adapter.search("test", page=1, page_size=10)
    assert result == []


def test_h_socarxiv_retries_on_5xx(monkeypatch):
    """SocArXiv search retries on a 5xx before succeeding."""
    from src.sources.socarxiv import SocArxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"data": [], "meta": {"total": 0}}

    adapter = SocArxivAdapter()
    monkeypatch.setattr(
        adapter.session, "get", MagicMock(side_effect=[bad, good])
    )
    with patch("src.sources.base.time.sleep"):
        result = adapter.search("test", page=1, page_size=10)
    assert result == []


def test_h_biorxiv_retries_on_5xx(monkeypatch):
    """bioRxiv/medRxiv search retries on a 5xx before succeeding."""
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good_data = {"messages": [{"total": 0}], "collection": []}

    adapter = BiorxivMedrxivAdapter()
    # search_recent is what with_retry wraps; patch it to return bad then good_data
    call_count = {"n": 0}

    def _search_recent(**kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.ConnectionError("transient")
        return good_data

    monkeypatch.setattr(adapter._api, "search_recent", _search_recent)
    with patch("src.sources.base.time.sleep"):
        result = adapter.search("test", page=1, page_size=10, filter_dict={"days_back": 7})
    assert result == []


def test_h_europepmc_get_total_retries_on_5xx(monkeypatch):
    """EuropePMC get_total retries on a 5xx before succeeding."""
    from src.sources.europepmc import EuropePmcAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"hitCount": "42"}

    adapter = EuropePmcAdapter()
    monkeypatch.setattr(
        adapter.session, "get", MagicMock(side_effect=[bad, good])
    )
    with patch("src.sources.base.time.sleep"):
        total = adapter.get_total("test")
    assert total == 42


def test_h_europepmc_get_by_id_retries_on_5xx(monkeypatch):
    """EuropePMC get_by_id retries on a 5xx before succeeding."""
    from src.sources.europepmc import EuropePmcAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"resultList": {"result": [{"id": "PMC123"}]}}

    adapter = EuropePmcAdapter()
    monkeypatch.setattr(
        adapter.session, "get", MagicMock(side_effect=[bad, good])
    )
    with patch("src.sources.base.time.sleep"):
        result = adapter.get_by_id("10.1234/test")
    assert result == {"id": "PMC123"}


def test_h_psyarxiv_get_total_retries_on_5xx(monkeypatch):
    """PsyArXiv get_total retries on a 5xx before succeeding."""
    from src.sources.psyarxiv import PsyArxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"meta": {"total": 7}}

    adapter = PsyArxivAdapter()
    monkeypatch.setattr(
        adapter.session, "get", MagicMock(side_effect=[bad, good])
    )
    with patch("src.sources.base.time.sleep"):
        total = adapter.get_total({"days_back": 7})
    assert total == 7


def test_h_socarxiv_get_total_retries_on_5xx(monkeypatch):
    """SocArXiv get_total retries on a 5xx before succeeding."""
    from src.sources.socarxiv import SocArxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"meta": {"total": 5}}

    adapter = SocArxivAdapter()
    monkeypatch.setattr(
        adapter.session, "get", MagicMock(side_effect=[bad, good])
    )
    with patch("src.sources.base.time.sleep"):
        total = adapter.get_total({"days_back": 7})
    assert total == 5


# ── download_pdf tri-state contract ───────────────────────────────────────────

def test_h_download_pdf_skip_when_no_pdf_url(tmp_path):
    """download_pdf returns 'skip' (not 'fail') when pdf_url is absent."""
    from agents.monitor import download_pdf

    record = {"canonical_id": "doi:10.1234/x"}  # no pdf_url key
    result = download_pdf(record, tmp_path)
    assert result == "skip", f"expected 'skip' for missing pdf_url, got {result!r}"


def test_h_download_pdf_ok_on_success(tmp_path):
    """download_pdf returns 'ok' when the download succeeds."""
    from agents.monitor import download_pdf

    record = {"canonical_id": "doi:10.1234/x", "pdf_url": "https://example.com/x.pdf"}
    fake_resp = MagicMock()
    fake_resp.content = b"%PDF-1.4 fake"
    fake_resp.raise_for_status = lambda: None

    with patch("requests.get", return_value=fake_resp):
        result = download_pdf(record, tmp_path)
    assert result == "ok", f"expected 'ok' on successful download, got {result!r}"


def test_h_monitor_no_false_exit2_for_skip(tmp_path, monkeypatch):
    """main() must NOT exit 2 when records lack pdf_url (skip, not fail)."""
    import agents.monitor as mon

    fake_record = {
        "canonical_id": "doi:10.1234/x",
        "title": "X",
        "abstract": "",
        "authors": "",
        "published_date": "2024-01-01",
        "doi": "10.1234/x",
        "source": "europepmc",
        "flags": {"retracted": False, "corrected": False, "fulltext_reusable": False},
        # no pdf_url — should be a skip, not a failure
    }

    monkeypatch.setattr(mon, "load_filters", lambda path: [
        {"name": "t", "enabled": True, "terms": [["x"]], "operator": "AND"}
    ])
    monkeypatch.setattr(mon, "run_search", lambda *a, **kw: [fake_record])
    monkeypatch.setattr(mon, "filter_papers", lambda records, f: records)

    download_dir = tmp_path / "pdfs"
    download_dir.mkdir()

    exit_code = mon.main(["--all", "--download-dir", str(download_dir)])
    assert exit_code == 0, (
        f"exit code must be 0 when pdf_url is absent (skip ≠ fail); got {exit_code}"
    )
