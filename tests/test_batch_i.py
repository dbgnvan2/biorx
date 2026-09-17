"""
Batch I test suite — P2/P19 mid-pagination partial failure (2026-09-16).

When a source raises SourceUnavailableError after it has already yielded records
(mid-pagination), _search_source must:
  1. Emit an on_status message containing FAILURE_STATUS_MARKER ("— skipped") so
     monitor.py counts it as a source failure.
  2. Return the records already fetched (not zero).
  3. Cause monitor.py to exit 2, not 0.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── _search_source emits partial-failure marker on mid-pagination error ────────

def _make_orch():
    """Return a bare SourceOrchestrator with no adapters registered."""
    from src.sources.orchestrator import SourceOrchestrator
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = {}
    return orch


def _make_adapter(page_size, fail_on_page=2):
    """
    Return a mock adapter that returns `page_size` records on page 1,
    then raises SourceUnavailableError on `fail_on_page`.
    """
    from src.sources.errors import SourceUnavailableError
    from src.sources.dedup import Deduplicator

    call_count = {"n": 0}

    def _search(query, page=1, **kw):
        call_count["n"] += 1
        if page < fail_on_page:
            return [{"doi": f"10.1234/{page}_{i}", "title": f"P{i}"} for i in range(page_size)]
        raise SourceUnavailableError("transient 503")

    adapter = MagicMock()
    adapter.source_name = "europepmc"
    adapter.source_trust_weight = 1.0
    adapter.last_total = 0
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
    return adapter


def test_i_mid_pagination_emits_skipped_marker():
    """
    When SourceUnavailableError fires on page 2 (after page 1 yielded records),
    on_status must receive a message containing '— skipped'.
    Without the fix, on_status is never called and monitor.py exits 0.
    """
    from src.sources.dedup import Deduplicator

    orch = _make_orch()
    adapter = _make_adapter(page_size=orch.PAGE_SIZE, fail_on_page=2)

    status_messages: list[str] = []

    orch._search_source(
        "europepmc", adapter, "q", {}, Deduplicator(),
        None, None, None,
        max_results=10_000,
        on_status=lambda msg: status_messages.append(msg),
    )

    skipped = [m for m in status_messages if "— skipped" in m]
    assert skipped, (
        f"expected at least one on_status call containing '— skipped'; "
        f"got: {status_messages!r}"
    )


def test_i_mid_pagination_returns_already_fetched_records():
    """
    Records yielded before the mid-pagination failure must still be returned.
    The truncation is surfaced via on_status, not by discarding results.
    """
    from src.sources.dedup import Deduplicator

    orch = _make_orch()
    page_size = orch.PAGE_SIZE
    adapter = _make_adapter(page_size=page_size, fail_on_page=2)

    count = orch._search_source(
        "europepmc", adapter, "q", {}, Deduplicator(),
        None, None, None,
        max_results=10_000,
        on_status=lambda msg: None,
    )

    assert count == page_size, (
        f"expected {page_size} records from page 1; got {count}"
    )


def test_i_mid_pagination_no_marker_emitted_when_on_status_is_none():
    """
    When on_status is None, the mid-pagination break must not raise.
    Regression guard: the fix must not break callers that pass no on_status.
    """
    from src.sources.dedup import Deduplicator

    orch = _make_orch()
    adapter = _make_adapter(page_size=orch.PAGE_SIZE, fail_on_page=2)

    # Must not raise even with on_status=None
    count = orch._search_source(
        "europepmc", adapter, "q", {}, Deduplicator(),
        None, None, None,
        max_results=10_000,
        on_status=None,
    )
    assert count > 0  # page-1 records still returned


def test_i_zero_fetch_still_raises():
    """
    When fetched==0 at the time of SourceUnavailableError, _search_source must
    still re-raise so search() can emit 'unavailable — skipped'. The mid-pagination
    fix must not suppress this path.
    """
    from src.sources.errors import SourceUnavailableError
    from src.sources.dedup import Deduplicator

    orch = _make_orch()
    adapter = _make_adapter(page_size=orch.PAGE_SIZE, fail_on_page=1)

    with pytest.raises(SourceUnavailableError):
        orch._search_source(
            "europepmc", adapter, "q", {}, Deduplicator(),
            None, None, None,
            max_results=10_000,
            on_status=lambda msg: None,
        )


def test_i_monitor_exits_2_on_mid_pagination_failure(tmp_path, monkeypatch):
    """
    monitor.main() must exit 2 when a source fails mid-pagination and emits
    the partial-failure marker. Before the fix, it exited 0 on a truncated set.
    """
    import agents.monitor as mon
    from src.sources.orchestrator import SourceOrchestrator

    MARKER = mon.FAILURE_STATUS_MARKER  # "— skipped"

    def fake_search(filter_dict, source_selection=None, on_batch=None, on_progress=None,
                    on_status=None, should_stop=None, max_results=200):
        # Simulate a source that yielded page 1, then failed mid-pagination
        if on_status:
            on_status(f"Europe PMC partial {MARKER}")
        return []

    orch = MagicMock()
    orch.search.side_effect = fake_search

    monkeypatch.setattr(mon, "load_sources_config", lambda: {})
    monkeypatch.setattr(mon, "SourceOrchestrator", lambda cfg: orch)
    monkeypatch.setattr(mon, "load_filters", lambda path: [
        {"name": "t", "enabled": True, "terms": [["x"]], "operator": "AND",
         "days_back": 7, "text_groups": [], "authors": [],
         "source_selection": {"all": True, "selected": []}}
    ])

    exit_code = mon.main(["--all"])
    assert exit_code == 2, (
        f"monitor must exit 2 when a partial-failure marker is emitted; got {exit_code}"
    )
