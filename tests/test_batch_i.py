"""
Batch I test suite — fixes for test-qa findings (2026-09-17).

P27  — conftest fallback: test the literal in source, not the import
P19  — FAILURE_STATUS_MARKER imported from orchestrator; all 4 emission sites use it;
        message format "<label> — skipped (<qualifier>)"; generic-Exception path covered
P2/P1 — RateLimitedError emits "— skipped" marker; mid-pagination partial-skip test
P21/P27 — per-adapter retry-on-5xx behaviour tests (psyarxiv, socarxiv, biorxiv_medrxiv, crossref)
P·mutation — with_retry backoff on HTTPError & RequestException paths; 4xx/5xx boundary;
              exhaustion; _search_source max_results boundary, date-range branch,
              fully-filtered page continue, bioRxiv _total progress
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))


# ── P27: conftest fallback literal matches db.py constant ─────────────────────

def test_i_conftest_fallback_literal_matches_db_constant():
    """The 'except ImportError' fallback literal in conftest.py must equal
    src.db.DEFAULT_DATA_DIR, checked by reading the source — not by importing
    conftest (which in a normal run imports the real constant, making both names
    the same object and the assertion a tautology)."""
    from src.db import DEFAULT_DATA_DIR as db_constant

    conftest_src = (Path(__file__).parent / "conftest.py").read_text()
    # Find the fallback assignment: DEFAULT_DATA_DIR = "~/preprints"
    import re
    m = re.search(r'DEFAULT_DATA_DIR\s*=\s*(["\'])(.+?)\1', conftest_src)
    assert m, "Could not find the DEFAULT_DATA_DIR fallback literal in conftest.py"
    literal_value = m.group(2)
    assert literal_value == db_constant, (
        f"conftest.py fallback {literal_value!r} != src.db.DEFAULT_DATA_DIR "
        f"{db_constant!r} — update the fallback string in conftest.py"
    )


# ── P19: FAILURE_STATUS_MARKER is imported from orchestrator ──────────────────

def test_i_all_failure_paths_emit_the_marker():
    """Every failure path in orchestrator.search() / _search_source must emit a
    status string containing FAILURE_STATUS_MARKER, so monitor.py's split-based
    detection works for all four cases: unavailable, error, rate-limited, partial.
    This is a behavioural test of the constant — not a literal re-pin."""
    from src.sources.orchestrator import SourceOrchestrator, FAILURE_STATUS_MARKER
    from src.sources.errors import SourceUnavailableError, RateLimitedError

    # We already have dedicated tests for unavailable (test_g_failure_detection_round_trip),
    # generic error (test_i_failure_detection_generic_exception_path),
    # rate-limited (test_i_rate_limited_error_emits_skipped_status), and
    # mid-pagination partial (test_i_mid_pagination_failure_emits_partial_skipped).
    # Here we verify that for each path the emitted string can be split on the marker
    # and yields a non-empty label before it — i.e. the message format is
    # "<label> <marker> ..." rather than "... <label> <marker>" (regression for the
    # rate-limited ordering bug where "rate-limited" was inserted before the marker).

    for exc_factory, label_key in [
        (lambda: SourceUnavailableError("down"), "unavailable"),
        (lambda: RuntimeError("crash"), "error"),
    ]:
        class _FailingAdapter:
            source_name = "europepmc"
            source_trust_weight = 1.0
            _exc = staticmethod(exc_factory)

            def search(self, *a, **kw):
                raise self._exc()

            def normalize(self, raw): raise NotImplementedError
            def get_by_id(self, id_): return None

        orch = SourceOrchestrator({})
        orch._search_adapters["europepmc"] = _FailingAdapter()
        msgs = []
        orch.search(
            filter_dict={"text_groups": [{"both": "t"}], "days_back": 7},
            source_selection={"all": False, "selected": ["europepmc"]},
            on_status=msgs.append,
        )
        failure_msgs = [m for m in msgs if FAILURE_STATUS_MARKER in m]
        assert failure_msgs, f"No failure message for {label_key}: {msgs}"
        for m in failure_msgs:
            label_part = m.split(FAILURE_STATUS_MARKER)[0].strip()
            assert label_part, (
                f"Label before marker is empty for {label_key}: {m!r}. "
                "Message format must be '<label> <marker> ...' not '... <marker> <label>'"
            )
            assert "Europe PMC" in label_part, (
                f"Unexpected label part {label_part!r} for {label_key}"
            )


def test_i_failure_detection_generic_exception_path():
    """The generic-Exception path in search() emits '<label> error — skipped'.
    Complements test_g_failure_detection_round_trip which covers SourceUnavailableError."""
    from src.sources.orchestrator import SourceOrchestrator, FAILURE_STATUS_MARKER

    class _AlwaysErrors:
        source_name = "europepmc"
        source_trust_weight = 1.0

        def search(self, *a, **kw):
            raise RuntimeError("unexpected internal error")

        def normalize(self, raw):
            raise NotImplementedError

        def get_by_id(self, identifier):
            return None

    orch = SourceOrchestrator({})
    orch._search_adapters["europepmc"] = _AlwaysErrors()

    status_messages: list = []
    orch.search(
        filter_dict={"text_groups": [{"both": "test"}], "days_back": 7},
        source_selection={"all": False, "selected": ["europepmc"]},
        on_status=status_messages.append,
    )

    failure_msgs = [m for m in status_messages if FAILURE_STATUS_MARKER in m]
    assert failure_msgs, (
        f"Expected a '— skipped' status for generic Exception; got: {status_messages!r}"
    )


# ── P2/P1: RateLimitedError emits the failure marker ─────────────────────────

def test_i_rate_limited_error_emits_skipped_status():
    """_search_source must emit a '— skipped' status on RateLimitedError so that
    monitor.py counts the source as failed (P2: no silent drops)."""
    from src.sources.errors import RateLimitedError
    from src.sources.orchestrator import SourceOrchestrator, FAILURE_STATUS_MARKER
    from src.sources.dedup import Deduplicator

    class _RateLimitedAdapter:
        source_name = "psyarxiv"
        source_trust_weight = 0.75

        def search(self, *a, **kw):
            raise RateLimitedError("429 hit")

        def normalize(self, raw):
            raise NotImplementedError

        def get_by_id(self, identifier):
            return None

    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = {"psyarxiv": _RateLimitedAdapter()}

    status_messages: list = []
    orch.search(
        filter_dict={"text_groups": [{"both": "test"}], "days_back": 7},
        source_selection={"all": False, "selected": ["psyarxiv"]},
        on_status=status_messages.append,
    )

    failure_msgs = [m for m in status_messages if FAILURE_STATUS_MARKER in m]
    assert failure_msgs, (
        f"Expected a '— skipped' status for RateLimitedError; got: {status_messages!r}"
    )


def test_i_mid_pagination_failure_emits_partial_skipped():
    """A source that succeeds on page 1 then fails on page 2 must emit
    '<label> partial — skipped' so monitor counts it as a failure."""
    from src.sources.errors import SourceUnavailableError
    from src.sources.orchestrator import SourceOrchestrator, FAILURE_STATUS_MARKER
    from src.sources.dedup import Deduplicator
    from src.sources.schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id

    def _make_record(doi):
        cid = make_canonical_id(doi=doi, title="T", first_author="A", year=2024)
        return CanonicalRecord(
            canonical_id=cid, title="T", abstract="", authors=[],
            year=2024, published_date="2024-01-01", document_type="article",
            is_preprint=False, journal_or_server="J",
            doi=doi, pmid="", pmcid="",
            source_url="", best_oa_url="", pdf_url="",
            license="", oa_status="open", subjects=[], keywords=[],
            source_hits=[SourceHit(source="europepmc", source_record_id=doi, fetched_at="2024-01-01")],
            flags=RecordFlags(), source_trust_weight=1.0,
        )

    page_size = SourceOrchestrator.PAGE_SIZE
    call_count = [0]

    class _FailsOnPage2:
        last_page_size = page_size
        last_total = 0

        def search(self, query, page=1, page_size=None, **kw):
            call_count[0] += 1
            if page == 1:
                self.last_page_size = page_size
                # Return a full page so pagination continues
                return [{"i": i} for i in range(page_size)]
            raise SourceUnavailableError("server died on page 2")

        def normalize(self, raw):
            return _make_record(f"10.1234/mid{raw['i']}")

        def get_by_id(self, identifier):
            return None

    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = {"europepmc": _FailsOnPage2()}

    status_messages: list = []
    orch.search(
        filter_dict={"text_groups": [{"both": "test"}], "days_back": 7},
        source_selection={"all": False, "selected": ["europepmc"]},
        on_status=status_messages.append,
    )

    failure_msgs = [m for m in status_messages if FAILURE_STATUS_MARKER in m]
    assert failure_msgs, (
        f"Expected a partial '— skipped' status on mid-pagination failure; "
        f"got: {status_messages!r}"
    )
    assert any("partial" in m for m in failure_msgs), (
        f"Expected 'partial' qualifier in the failure status; got: {failure_msgs!r}"
    )
    # Label must come BEFORE the marker so monitor.py's split extracts the right name
    for m in failure_msgs:
        label_part = m.split(FAILURE_STATUS_MARKER)[0].strip()
        assert label_part, f"Label before marker is empty: {m!r}"


# ── P·mutation: with_retry backoff on HTTPError and RequestException paths ────

class TestWithRetryMutationGaps:
    """Pin the mutation-surviving branches: exception-path backoff schedule,
    4xx/5xx boundary, and HTTPError-path exhaustion."""

    def setup_method(self):
        from src.sources.base import with_retry
        from src.sources.errors import SourceUnavailableError
        self.with_retry = with_retry
        self.SourceUnavailableError = SourceUnavailableError

    def _http_error(self, status):
        r = MagicMock(spec=requests.Response)
        r.status_code = status
        exc = requests.HTTPError(response=r)
        return exc

    # ── Backoff schedule on HTTPError 5xx path ────────────────────────────────

    def test_i_http_error_5xx_backoff_schedule(self):
        """HTTPError 5xx path sleeps 1s then 2s (2**(attempt-1))."""
        exc = self._http_error(503)
        fn = MagicMock(side_effect=exc)
        sleep_calls = []
        with patch("src.sources.base.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with pytest.raises(self.SourceUnavailableError):
                self.with_retry(fn, max_attempts=3, source_label="X")
        assert sleep_calls == [1, 2], f"expected [1, 2], got {sleep_calls}"

    def test_i_request_exception_backoff_schedule(self):
        """RequestException path sleeps 1s then 2s (2**(attempt-1))."""
        fn = MagicMock(side_effect=requests.ConnectionError("boom"))
        sleep_calls = []
        with patch("src.sources.base.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with pytest.raises(self.SourceUnavailableError):
                self.with_retry(fn, max_attempts=3, source_label="X")
        assert sleep_calls == [1, 2], f"expected [1, 2], got {sleep_calls}"

    # ── 4xx/5xx boundary ──────────────────────────────────────────────────────

    def test_i_status_499_is_not_retried(self):
        """Status 499 (< 500) is a 4xx — returned without retry."""
        r = MagicMock(spec=requests.Response)
        r.status_code = 499
        r.ok = False
        fn = MagicMock(return_value=r)
        result = self.with_retry(fn, max_attempts=3, source_label="X")
        assert result is r
        assert fn.call_count == 1

    def test_i_status_500_is_retried(self):
        """Status 500 (>= 500) triggers retry."""
        bad = MagicMock(spec=requests.Response)
        bad.status_code = 500
        bad.ok = False
        good = MagicMock(spec=requests.Response)
        good.status_code = 200
        good.ok = True
        fn = MagicMock(side_effect=[bad, good])
        with patch("src.sources.base.time.sleep"):
            result = self.with_retry(fn, max_attempts=3, source_label="X")
        assert result is good
        assert fn.call_count == 2

    def test_i_http_error_499_not_retried(self):
        """HTTPError with 499 (< 500) is re-raised without retry."""
        exc = self._http_error(499)
        fn = MagicMock(side_effect=exc)
        with pytest.raises(requests.HTTPError):
            self.with_retry(fn, max_attempts=3, source_label="X")
        assert fn.call_count == 1

    def test_i_http_error_500_retried(self):
        """HTTPError with 500 (>= 500) triggers retry."""
        exc_500 = self._http_error(500)
        good = MagicMock(spec=requests.Response)
        good.status_code = 200
        good.ok = True
        fn = MagicMock(side_effect=[exc_500, good])
        with patch("src.sources.base.time.sleep"):
            result = self.with_retry(fn, max_attempts=3, source_label="X")
        assert result is good
        assert fn.call_count == 2

    # ── Exhaustion boundary on HTTPError path ─────────────────────────────────

    def test_i_http_error_5xx_exhaustion_at_max_attempts(self):
        """HTTPError 5xx path raises SourceUnavailableError after exactly max_attempts."""
        exc = self._http_error(503)
        fn = MagicMock(side_effect=exc)
        with patch("src.sources.base.time.sleep"):
            with pytest.raises(self.SourceUnavailableError):
                self.with_retry(fn, max_attempts=4, source_label="X")
        assert fn.call_count == 4

    def test_i_max_attempts_default_is_3(self):
        """Default max_attempts is 3 (not 2, not 4)."""
        bad = MagicMock(spec=requests.Response)
        bad.status_code = 503
        bad.ok = False
        fn = MagicMock(return_value=bad)
        with patch("src.sources.base.time.sleep"):
            with pytest.raises(self.SourceUnavailableError):
                self.with_retry(fn, source_label="X")
        assert fn.call_count == 3


# ── P21/P27: per-adapter retry-on-5xx behaviour tests ─────────────────────────

def test_i_psyarxiv_retries_on_5xx(monkeypatch):
    """PsyArXiv search retries on 5xx before succeeding."""
    from src.sources.psyarxiv import PsyArxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"data": [], "meta": {"total": 0}}

    adapter = PsyArxivAdapter()
    monkeypatch.setattr(adapter.session, "get", MagicMock(side_effect=[bad, good]))
    with patch("src.sources.base.time.sleep"):
        result = adapter.search("test", page=1, page_size=10)
    assert result == []


def test_i_socarxiv_retries_on_5xx(monkeypatch):
    """SocArXiv search retries on 5xx before succeeding."""
    from src.sources.socarxiv import SocArxivAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"data": []}

    adapter = SocArxivAdapter()
    monkeypatch.setattr(adapter.session, "get", MagicMock(side_effect=[bad, good]))
    with patch("src.sources.base.time.sleep"):
        result = adapter.search("test", page=1, page_size=10)
    assert result == []


def test_i_biorxiv_medrxiv_retries_on_5xx(monkeypatch):
    """BiorxivMedrxivAdapter.search retries on 5xx before succeeding."""
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter
    import requests as req_mod

    good_resp = {"collection": [], "messages": [{"total": 0}]}

    call_count = [0]
    def fake_search_recent(**kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # Return a 5xx Response; with_retry detects status >= 500 and retries
            resp = MagicMock(spec=req_mod.Response)
            resp.status_code = 503
            resp.ok = False
            return resp
        return good_resp

    adapter = BiorxivMedrxivAdapter.__new__(BiorxivMedrxivAdapter)
    adapter._api = MagicMock()
    adapter._api.search_recent.side_effect = fake_search_recent
    adapter._api.parse_papers.return_value = []

    with patch("src.sources.base.time.sleep"):
        result = adapter.search("", page=1, filter_dict={"days_back": 7})

    # Retry fired: first call got 503, second returned the dict
    assert call_count[0] == 2, f"expected 2 calls, got {call_count[0]}"
    assert result == []


def test_i_crossref_get_by_id_retries_on_5xx(monkeypatch):
    """CrossrefAdapter.get_by_id retries on 5xx before succeeding."""
    from src.sources.crossref import CrossrefAdapter

    bad = MagicMock(spec=requests.Response)
    bad.status_code = 503
    bad.ok = False

    good = MagicMock(spec=requests.Response)
    good.status_code = 200
    good.ok = True
    good.json.return_value = {"message": {"DOI": "10.1234/test", "title": ["T"], "abstract": ""}}

    adapter = CrossrefAdapter()
    monkeypatch.setattr(adapter.session, "get", MagicMock(side_effect=[bad, good]))
    with patch("src.sources.base.time.sleep"):
        result = adapter.get_by_id("10.1234/test")
    assert result is not None


# ── P-coverage: surviving mutants in _search_source pagination/progress ───────

def _orch_bare():
    """Minimal SourceOrchestrator with no registered adapters."""
    from src.sources.orchestrator import SourceOrchestrator
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = {}
    return orch


def _make_record(doi):
    """Make a CanonicalRecord with a title derived from doi so title+author+year key is unique."""
    from src.sources.schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
    # Title must differ per record: Deduplicator keys on title+first_author+year
    title = f"Paper {doi}"
    first_author = "Smith"
    cid = make_canonical_id(doi=doi, title=title, first_author=first_author, year=2024)
    return CanonicalRecord(
        canonical_id=cid, title=title, abstract="", authors=[
            AuthorRecord(display_name="Smith J", sequence=1)
        ],
        year=2024, published_date="2024-01-01", document_type="article",
        is_preprint=False, journal_or_server="J",
        doi=doi, pmid="", pmcid="",
        source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="open", subjects=[], keywords=[],
        source_hits=[SourceHit(source="europepmc", source_record_id=doi, fetched_at="2024-01-01")],
        flags=RecordFlags(), source_trust_weight=1.0,
    )


def test_i_search_source_stops_at_exact_max_results():
    """fetched >= max_results (not >) exits the loop at the boundary, not one record later."""
    from src.sources.dedup import Deduplicator
    from src.sources.orchestrator import SourceOrchestrator

    orch = _orch_bare()
    page_size = orch.PAGE_SIZE
    max_results = page_size  # exactly one page worth

    pages_called = [0]

    class _CountingAdapter:
        last_page_size = page_size
        last_total = 0

        def search(self, query, page=1, page_size=None, **kw):
            pages_called[0] += 1
            self.last_page_size = page_size
            return [{"i": (page - 1) * page_size + j} for j in range(page_size)]

        def normalize(self, raw):
            return _make_record(f"10.1234/r{raw['i']}")

    fetched = orch._search_source(
        "europepmc", _CountingAdapter(), "q", {}, Deduplicator(),
        None, None, None, max_results=max_results,
    )

    assert fetched == max_results, f"expected {max_results}, got {fetched}"
    assert pages_called[0] == 1, f"loop should stop after page 1; called {pages_called[0]} times"


def test_i_search_source_passes_filter_dict_to_date_range_adapters():
    """psyarxiv / socarxiv / biorxiv_medrxiv / arxiv receive filter_dict as a kwarg;
    europepmc does not. Regression for the `in -> not in` mutant."""
    from src.sources.dedup import Deduplicator
    from src.sources.orchestrator import SourceOrchestrator

    orch = _orch_bare()
    received_kwargs: dict = {}

    class _KwargCapture:
        last_page_size = 0
        last_total = 0

        def search(self, query, page=1, page_size=25, **kw):
            received_kwargs.update(kw)
            self.last_page_size = 0  # short page → stop
            return []

        def normalize(self, raw):
            raise NotImplementedError

    fd = {"days_back": 14, "text_groups": []}

    # psyarxiv branch should receive filter_dict
    orch._search_source(
        "psyarxiv", _KwargCapture(), "q", fd, Deduplicator(),
        None, None, None, max_results=10,
    )
    assert "filter_dict" in received_kwargs, (
        "psyarxiv _search_source did not pass filter_dict to adapter.search()"
    )
    assert received_kwargs["filter_dict"] is fd

    # europepmc branch should NOT receive filter_dict
    received_kwargs.clear()
    orch._search_source(
        "europepmc", _KwargCapture(), "q", fd, Deduplicator(),
        None, None, None, max_results=10,
    )
    assert "filter_dict" not in received_kwargs, (
        "europepmc _search_source should not pass filter_dict to adapter.search()"
    )


def test_i_search_source_continues_paging_after_fully_filtered_full_page():
    """When a full page's records are all duplicates (dedup removes them) but the
    source's last_page_size is PAGE_SIZE, pagination must continue — not stop.
    Regression for the `page += 1` → `page += 0` mutant on the filtered-page path."""
    from src.sources.dedup import Deduplicator
    from src.sources.orchestrator import SourceOrchestrator

    orch = _orch_bare()
    page_size = orch.PAGE_SIZE

    # Use a shared deduplicator that already has the first page's records.
    dedup = Deduplicator()
    # Pre-populate with page-1 records so page 1 is fully "filtered" (all dups).
    for i in range(page_size):
        dedup.add(_make_record(f"10.1234/dup{i}"))

    pages_called = [0]

    class _FullThenShortAdapter:
        last_page_size = page_size
        last_total = 0

        def search(self, query, page=1, page_size=None, **kw):
            pages_called[0] += 1
            self.last_page_size = page_size if page == 1 else 1
            if page == 1:
                # Return the same records dedup already has → all filtered
                return [{"i": i} for i in range(page_size)]
            # page 2: one genuinely new record
            return [{"i": 99999}]

        def normalize(self, raw):
            return _make_record(f"10.1234/dup{raw['i']}")

    fetched = orch._search_source(
        "europepmc", _FullThenShortAdapter(), "q", {}, dedup,
        None, None, None, max_results=1000,
    )

    assert pages_called[0] == 2, (
        f"Expected 2 pages (continue past fully-filtered page); got {pages_called[0]}"
    )
    # The new record from page 2 was fetched
    assert fetched == 1


def test_i_biorxiv_total_is_reported_to_progress():
    """bioRxiv _total field on raw records is passed to on_progress as src_total."""
    from src.sources.dedup import Deduplicator
    from src.sources.orchestrator import SourceOrchestrator

    orch = _orch_bare()

    progress_reports: list = []

    class _BiorxivAdapter:
        last_page_size = 1
        last_total = 0

        def search(self, query, page=1, page_size=None, **kw):
            self.last_page_size = 1  # short page (< PAGE_SIZE) → stop after one call
            return [{"i": 0, "_total": 42}]

        def normalize(self, raw):
            return _make_record(f"10.1234/bio{raw['i']}")

    orch._search_source(
        "biorxiv_medrxiv", _BiorxivAdapter(), "q", {}, Deduplicator(),
        None, lambda fetched, total: progress_reports.append((fetched, total)), None,
        max_results=1000,
    )

    assert progress_reports, "expected at least one progress report"
    totals = [total for _, total in progress_reports]
    assert any(t == 42 for t in totals), (
        f"expected src_total=42 from _total field; got: {progress_reports}"
    )
