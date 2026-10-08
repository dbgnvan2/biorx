"""
Integration tests for SourceOrchestrator routing and enrichment.
All network calls are mocked.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch

from src.sources.schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags
from src.sources.orchestrator import SourceOrchestrator


def _make_record(doi="10.1234/test", source="europepmc", title="Test Paper"):
    from src.sources.schema import make_canonical_id
    cid = make_canonical_id(doi=doi, title=title, first_author="Smith", year=2024)
    return CanonicalRecord(
        canonical_id=cid, title=title, abstract="Test abstract",
        authors=[AuthorRecord(display_name="Smith J", sequence=1)],
        year=2024, published_date="2024-01-01", document_type="article",
        is_preprint=False, journal_or_server="Test Journal",
        doi=doi, pmid="", pmcid="",
        source_url="https://example.com", best_oa_url="", pdf_url="",
        license="cc_by", oa_status="open", subjects=[], keywords=[],
        source_hits=[SourceHit(source=source, source_record_id=doi, fetched_at="2024-01-01")],
        flags=RecordFlags(), source_trust_weight=1.0,
    )


def _config(europepmc=True, psyarxiv=True, biorxiv=False):
    return {
        "publication_sources": {
            "europepmc":       {"enabled": europepmc,  "default_selected": europepmc},
            "psyarxiv":        {"enabled": psyarxiv,   "default_selected": psyarxiv},
            "biorxiv_medrxiv": {"enabled": biorxiv,    "default_selected": biorxiv},
            "crossref":        {"enabled": False},
            "unpaywall":       {"enabled": False},
        },
        "unpaywall_email": "test@example.com",
        "crossref_user_agent": "Test/1.0",
    }


def test_all_sources_queries_all_enabled_adapters():
    """All Sources = ON should query all enabled search adapters."""
    config = _config(europepmc=True, psyarxiv=True)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    mock_epmc = MagicMock()
    mock_epmc.search.return_value = []
    mock_psya = MagicMock()
    mock_psya.search.return_value = []

    orch._search_adapters = {"europepmc": mock_epmc, "psyarxiv": mock_psya}

    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "stress"}]},
        source_selection={"all": True, "selected": []},
    )

    mock_epmc.search.assert_called()
    mock_psya.search.assert_called()


def test_selecting_only_europepmc_skips_psyarxiv():
    """Selecting Europe PMC only should not call PsyArXiv."""
    config = _config(europepmc=True, psyarxiv=True)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    mock_epmc = MagicMock()
    mock_epmc.search.return_value = []
    mock_psya = MagicMock()
    mock_psya.search.return_value = []

    orch._search_adapters = {"europepmc": mock_epmc, "psyarxiv": mock_psya}

    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": False, "selected": ["europepmc"]},
    )

    mock_epmc.search.assert_called()
    mock_psya.search.assert_not_called()


def test_results_are_deduplicated():
    """Same DOI from two sources should produce one canonical record."""
    config = _config(europepmc=True, psyarxiv=True)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    record_a = _make_record(doi="10.1234/same", source="europepmc")
    record_b = _make_record(doi="10.1234/same", source="psyarxiv")

    mock_epmc = MagicMock()
    mock_epmc.search.return_value = [{"id": "fake"}]
    mock_epmc.normalize.return_value = record_a

    mock_psya = MagicMock()
    mock_psya.search.return_value = [{"id": "fake2"}]
    mock_psya.normalize.return_value = record_b

    orch._search_adapters = {"europepmc": mock_epmc, "psyarxiv": mock_psya}

    results = orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
    )

    assert len(results) == 1
    assert len(results[0].source_hits) == 2


def test_source_unavailable_does_not_crash():
    """If a source fails, the search should continue with remaining sources."""
    from src.sources.errors import SourceUnavailableError

    config = _config(europepmc=True, psyarxiv=True)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    mock_epmc = MagicMock()
    mock_epmc.search.side_effect = SourceUnavailableError("Connection refused")

    record_psya = _make_record(doi="10.1234/psya", source="psyarxiv")
    mock_psya = MagicMock()
    mock_psya.search.return_value = [{}]
    mock_psya.normalize.return_value = record_psya

    orch._search_adapters = {"europepmc": mock_epmc, "psyarxiv": mock_psya}

    results = orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
    )

    assert len(results) == 1
    assert results[0].doi == "10.1234/psya"


def test_crossref_enriches_records():
    """Crossref should be called for records with DOIs."""
    config = _config(europepmc=True, psyarxiv=False)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._unpaywall = None

    mock_crossref = MagicMock()
    orch._crossref = mock_crossref

    record = _make_record(doi="10.1234/enrich")
    mock_epmc = MagicMock()
    mock_epmc.search.return_value = [{}]
    mock_epmc.normalize.return_value = record
    orch._search_adapters = {"europepmc": mock_epmc}

    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
    )

    mock_crossref.enrich.assert_called_once()


def test_on_batch_callback_called():
    """on_batch should be called for each page of results."""
    config = _config(europepmc=True, psyarxiv=False)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    record = _make_record(doi="10.1234/batch")
    mock_epmc = MagicMock()
    mock_epmc.search.return_value = [{}]
    mock_epmc.normalize.return_value = record
    orch._search_adapters = {"europepmc": mock_epmc}

    batches = []
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
        on_batch=batches.append,
    )

    assert len(batches) > 0


def test_duplicate_across_sources_streamed_once():
    """A paper found in two overlapping sources (e.g. EuropePMC + PubMed) must be
    streamed to on_batch only once, so the displayed count matches the unique set
    that gets saved (regression for the 121-found / 70-saved discrepancy)."""
    config = _config(europepmc=True, psyarxiv=False)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    # Both sources return the SAME DOI.
    epmc_rec = _make_record(doi="10.1234/overlap", source="europepmc")
    pm_rec   = _make_record(doi="10.1234/overlap", source="pubmed")
    mock_epmc = MagicMock(); mock_epmc.search.return_value = [{}]; mock_epmc.normalize.return_value = epmc_rec
    mock_pm   = MagicMock(); mock_pm.search.return_value = [{}];   mock_pm.normalize.return_value = pm_rec
    orch._search_adapters = {"europepmc": mock_epmc, "pubmed": mock_pm}

    streamed = []
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
        on_batch=lambda recs: streamed.extend(recs),
    )

    # Both sources were queried, but the duplicate is streamed only once.
    mock_epmc.search.assert_called()
    mock_pm.search.assert_called()
    assert len(streamed) == 1, [r.doi for r in streamed]


def test_e2_1_on_status_emits_per_source():
    """E2.1: on_status emits a 'Searching <label>…' message for each active source."""
    config = _config(europepmc=True, psyarxiv=True)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    mock_epmc = MagicMock(); mock_epmc.search.return_value = []
    mock_psya = MagicMock(); mock_psya.search.return_value = []
    orch._search_adapters = {"europepmc": mock_epmc, "psyarxiv": mock_psya}

    messages = []
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "stress"}]},
        source_selection={"all": True, "selected": []},
        on_status=messages.append,
    )

    assert any("Searching Europe PMC" in m for m in messages), messages
    assert any("Searching PsyArXiv" in m for m in messages), messages


def test_e2_2_progress_reports_known_total():
    """E2.2: on_progress reports a real total (> fetched), not the degenerate
    fetched == total emitted previously."""
    config = _config(europepmc=True, psyarxiv=False)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._crossref  = None
    orch._unpaywall = None

    record = _make_record(doi="10.1234/total")
    mock_epmc = MagicMock()
    mock_epmc.search.return_value = [{}]      # one short page -> stops after page 1
    mock_epmc.normalize.return_value = record
    mock_epmc.last_total = 120                # source reports 120 total hits
    orch._search_adapters = {"europepmc": mock_epmc}

    progress = []
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
        on_progress=lambda fetched, total: progress.append((fetched, total)),
    )

    assert progress, "expected at least one progress update"
    # The reported total should reflect the source's hit count, not just fetched.
    assert any(total >= 120 and fetched < total for fetched, total in progress), progress


def test_e2_3_enrichment_emits_status():
    """E2.3: the enrichment phase emits an 'Enriching N papers…' status message."""
    config = _config(europepmc=True, psyarxiv=False)
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = config
    orch._unpaywall = None
    orch._crossref  = MagicMock()

    record = _make_record(doi="10.1234/enrichstatus")
    mock_epmc = MagicMock()
    mock_epmc.search.return_value = [{}]
    mock_epmc.normalize.return_value = record
    orch._search_adapters = {"europepmc": mock_epmc}

    messages = []
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
        source_selection={"all": True, "selected": []},
        on_status=messages.append,
    )

    assert any("Enriching" in m for m in messages), messages
    orch._crossref.enrich.assert_called()


def _two_record_orch():
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = _config(europepmc=True, psyarxiv=False)
    orch._crossref = MagicMock()
    orch._unpaywall = MagicMock()
    keep = _make_record(doi="10.1234/keep", title="Kept")
    drop = _make_record(doi="10.1234/drop", title="Dropped")
    mock_epmc = MagicMock()
    mock_epmc.search.return_value = [{}, {}]
    mock_epmc.normalize.side_effect = [keep, drop]
    mock_epmc.last_page_size = 2
    mock_epmc.last_total = 2
    orch._search_adapters = {"europepmc": mock_epmc}
    return orch, keep, drop


def test_fr2_1_enrich_only_limits_enrichment():
    """FR2.1: with enrich_only, a record the caller will discard gets no
    Crossref or Unpaywall call; the kept one gets both."""
    orch, keep, drop = _two_record_orch()
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "kept"}]},
        source_selection={"all": True, "selected": []},
        enrich_only=lambda r: r is keep,
    )
    assert [c.args[0] for c in orch._crossref.enrich.call_args_list] == [keep]
    assert [c.args[0] for c in orch._unpaywall.enrich.call_args_list] == [keep]


def test_fr3_1_enrich_progress_has_its_own_channel():
    """FR3.1 (orchestrator half): given on_enrich_progress, enrichment counts go
    there and never through on_progress, which carries the fetched count."""
    orch, keep, drop = _two_record_orch()
    fetch_progress, enrich_progress = [], []
    orch.search(
        filter_dict={"days_back": 7, "text_groups": [{"both": "kept"}]},
        source_selection={"all": True, "selected": []},
        on_progress=lambda a, b: fetch_progress.append((a, b)),
        on_enrich_progress=lambda a, b: enrich_progress.append((a, b)),
        enrich_only=lambda r: r is keep,
    )
    assert enrich_progress == [(0, 1), (1, 1)]
    assert fetch_progress and fetch_progress[-1][0] == 2


def test_i3_enrichment_outage_is_reported(caplog):
    """Issue 3: a Crossref lookup that failed (enrich returned False) or raised
    is counted and reported — WARNING log, status line, on_enrich_problem —
    instead of a debug line nobody sees. A "not found" (True) is not a failure."""
    import logging
    orch, keep, drop = _two_record_orch()
    orch._crossref.enrich.side_effect = [False, RuntimeError("boom")]
    orch._unpaywall.enrich.return_value = True
    problems, messages = [], []
    with caplog.at_level(logging.WARNING, logger="src.sources.orchestrator"):
        orch.search(
            filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
            source_selection={"all": True, "selected": []},
            on_status=messages.append,
            on_enrich_problem=lambda *a: problems.append(a),
        )
    # Since review M19 an exception is a program error, reported on its own
    # line, not counted as an outage the user should retry.
    assert problems == [("Crossref", 1, 2)]
    assert "Crossref failed for 1 of 2 papers" in messages
    assert "Crossref lookups hit a program error for 1 of 2 papers (details in the server log)" in messages
    assert any("Crossref lookups failed for 1 of 2" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records), "the error's traceback is logged"


def test_m19_code_error_not_counted_as_outage(caplog):
    """Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M19.
    An exception from our own code is a program error, logged with its
    traceback; it is not reported as a source outage the user should retry."""
    import logging
    orch, keep, drop = _two_record_orch()
    orch._crossref.enrich.side_effect = [RuntimeError("boom"), True]
    orch._unpaywall.enrich.return_value = True
    problems, messages = [], []
    with caplog.at_level(logging.WARNING, logger="src.sources.orchestrator"):
        orch.search(
            filter_dict={"days_back": 7, "text_groups": [{"both": "x"}]},
            source_selection={"all": True, "selected": []},
            on_status=messages.append,
            on_enrich_problem=lambda *a: problems.append(a),
        )
    assert problems == []
    assert not any("Crossref failed" in m for m in messages)
    assert "Crossref lookups hit a program error for 1 of 2 papers (details in the server log)" in messages
    assert any(r.exc_info for r in caplog.records)


# ── Page-end detection must use the source's page size (review finding 2) ─────

def _orch_for_pagination():
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = _config()
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = {}
    return orch


def test_filtered_entry_does_not_end_pagination_early():
    """
    An adapter that removes entries from a page (arXiv drops withdrawn papers)
    returns fewer records than the source sent. Using the returned length as the
    last-page signal would discard every remaining page.
    """
    from src.sources.dedup import Deduplicator

    orch = _orch_for_pagination()
    page_size = orch.PAGE_SIZE

    adapter = MagicMock()
    adapter.last_page_size = page_size          # source sent a full page...
    adapter.last_total = 0
    pages = {
        1: [{"i": i} for i in range(page_size - 1)],   # ...one was filtered out
        2: [{"i": 100}],
    }

    def _search(query, page=1, page_size=None, **kw):
        adapter.last_page_size = page_size if page in (1,) else 1
        return pages.get(page, [])

    adapter.search.side_effect = _search
    adapter.normalize.side_effect = lambda raw: _make_record(
        doi=f"10.1234/p{raw['i']}", title=f"Paper {raw['i']}"
    )

    fetched = orch._search_source(
        "arxiv", adapter, "q", {}, Deduplicator(),
        None, None, None, max_results=1000,
    )

    assert adapter.search.call_count == 2, "stopped after page 1"
    assert fetched == page_size, "records from page 2 were lost"


def test_page_shorter_than_page_size_still_ends_pagination():
    """The last page must still terminate the loop."""
    from src.sources.dedup import Deduplicator

    orch = _orch_for_pagination()
    adapter = MagicMock()
    adapter.last_total = 0
    adapter.last_page_size = 3
    adapter.search.return_value = [{"i": 1}, {"i": 2}, {"i": 3}]
    adapter.normalize.side_effect = lambda raw: _make_record(
        doi=f"10.1234/q{raw['i']}", title=f"Q {raw['i']}"
    )

    fetched = orch._search_source(
        "arxiv", adapter, "q", {}, Deduplicator(),
        None, None, None, max_results=1000,
    )
    assert adapter.search.call_count == 1
    assert fetched == 3


def test_adapters_without_a_page_size_attribute_are_unaffected():
    """Siblings that do not report last_page_size keep the old behaviour (P5)."""
    from src.sources.dedup import Deduplicator

    orch = _orch_for_pagination()

    class PlainAdapter:
        def __init__(self):
            self.calls = 0
        def search(self, query, page=1, page_size=25, **kw):
            self.calls += 1
            return [{"i": page}] if page == 1 else []
        def normalize(self, raw):
            return _make_record(doi=f"10.1234/r{raw['i']}", title=f"R {raw['i']}")

    adapter = PlainAdapter()
    fetched = orch._search_source(
        "europepmc", adapter, "q", {}, Deduplicator(),
        None, None, None, max_results=1000,
    )
    assert adapter.calls == 1      # short page ended it, as before
    assert fetched == 1


def test_full_pages_advance_page_until_max_pages():
    """A source that keeps returning full pages must advance the page cursor each
    iteration and terminate at MAX_PAGES_PER_SOURCE. Regression for the mutation
    `page += 1` -> `page += 0`, which spins forever: page_size_seen stays at
    PAGE_SIZE (never `< PAGE_SIZE`) and no source total is reported, so the only
    thing that ends the loop is the page cursor advancing."""
    from src.sources.dedup import Deduplicator

    orch = _orch_for_pagination()
    page_size = orch.PAGE_SIZE

    class FullPageAdapter:
        def __init__(self, page_size):
            self.page_size = page_size
            self.pages_seen = []
            self.last_page_size = 0
            self.last_total = 0

        def search(self, query, page=1, page_size=None, **kw):
            self.pages_seen.append(page)
            self.last_page_size = page_size
            # A distinct, full page of records on every call.
            base = (page - 1) * self.page_size
            return [{"i": base + i} for i in range(self.page_size)]

        def normalize(self, raw):
            return _make_record(doi=f"10.1234/full{raw['i']}", title=f"Full {raw['i']}")

    adapter = FullPageAdapter(page_size)
    fetched = orch._search_source(
        "europepmc", adapter, "q", {}, Deduplicator(),
        None, None, None, max_results=10_000,
    )

    assert adapter.pages_seen == list(range(1, orch.MAX_PAGES_PER_SOURCE + 1)), \
        adapter.pages_seen
    assert fetched == page_size * orch.MAX_PAGES_PER_SOURCE


# ── B1: one source cannot use up the whole run's budget ──────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B1
# Before the fix max_results was one budget shared by every source. Europe PMC
# runs first, so a broad filter filled it and later sources were never queried,
# with no status line (live: "Loneliness" searched Europe PMC only).

class _PagedAdapter:
    """Serves `total` distinct records in pages; reports hitCount like Europe PMC."""

    def __init__(self, prefix, total):
        self.prefix, self.total = prefix, total
        self.last_page_size = 0
        self.last_total = total
        self.calls = 0

    def search(self, query, page=1, page_size=50, **_):
        self.calls += 1
        start = (page - 1) * page_size
        ids = list(range(start, min(start + page_size, self.total)))
        self.last_page_size = len(ids)
        return [{"i": i} for i in ids]

    def normalize(self, raw):
        return _make_record(doi=f"10.1/{self.prefix}{raw['i']}", source=self.prefix,
                            title=f"{self.prefix} paper {raw['i']}")


def _orch_with(adapters):
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch._crossref = None
    orch._unpaywall = None
    orch._search_adapters = adapters
    return orch


def test_b1_first_source_cannot_starve_later_sources():
    big = _PagedAdapter("europepmc", total=1000)
    later = _PagedAdapter("psyarxiv", total=30)
    orch = _orch_with({"europepmc": big, "psyarxiv": later})

    records = orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                          {"all": True, "selected": []}, max_results=200)

    assert later.calls >= 1, "the second source was never queried"
    sources = [r.source_hits[0].source for r in records]
    assert sources.count("psyarxiv") == 30
    assert sources.count("europepmc") == 200


def test_b1_truncated_source_is_reported():
    big = _PagedAdapter("europepmc", total=1000)
    small = _PagedAdapter("psyarxiv", total=30)
    statuses = []
    orch = _orch_with({"europepmc": big, "psyarxiv": small})

    orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                {"all": True, "selected": []}, max_results=200,
                on_status=statuses.append)

    from src.sources.orchestrator import FAILURE_STATUS_MARKER
    truncated = [s for s in statuses if FAILURE_STATUS_MARKER in s and "(truncated)" in s]
    assert truncated == ["Europe PMC — skipped (truncated)"], statuses
    assert any("Europe PMC: 200 of 1,000 read" in s for s in statuses), statuses
    # A source read to the end is not reported as truncated.
    assert not any(s.startswith("PsyArXiv") and "truncated" in s for s in statuses)


def test_prog1_each_page_is_announced_before_it_is_requested():
    """A slow source must change the status line every page, not stay on
    "Searching…" until it finishes."""
    big = _PagedAdapter("psyarxiv", total=120)
    statuses = []
    orch = _orch_with({"psyarxiv": big})

    orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                {"all": True, "selected": []}, max_results=500,
                on_status=statuses.append)

    pages = [s for s in statuses if s.startswith("PsyArXiv: reading page")]
    assert len(pages) == big.calls, statuses
    assert pages[0].startswith("PsyArXiv: reading page 1…"), pages
    assert "so far" in pages[1], pages


def test_b1_page_cap_truncation_is_reported():
    big = _PagedAdapter("europepmc", total=5000)
    statuses = []
    orch = _orch_with({"europepmc": big})

    orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                {"all": True, "selected": []}, max_results=2000,
                on_status=statuses.append)

    assert big.calls == orch.MAX_PAGES_PER_SOURCE
    assert "Europe PMC — skipped (truncated)" in statuses, statuses


def test_b1_exact_fit_is_not_truncation():
    """A source with exactly max_results records was read to the end."""
    exact = _PagedAdapter("europepmc", total=200)
    statuses = []
    orch = _orch_with({"europepmc": exact})
    orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                {"all": True, "selected": []}, max_results=200,
                on_status=statuses.append)
    assert not any("truncated" in s for s in statuses), statuses


# ── B6: concurrent searches do not share paging state ─────────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B6
# One orchestrator (and its adapters) serves every job thread. Europe PMC kept
# its cursor on the adapter, so two interleaved searches reset each other's
# cursor and pages repeated or were skipped.

def test_b6_concurrent_searches_do_not_share_cursor():
    import threading
    from unittest.mock import MagicMock
    from src.sources.europepmc import EuropePmcAdapter

    barrier = threading.Barrier(2, timeout=5)
    per_page = 50
    calls = []                      # (query, cursor) as the server saw them
    lock = threading.Lock()

    def fake_get(self, url, params=None, timeout=None):
        # Server: two pages per query. Cursor "*" is page 1, "<query>|2" is
        # page 2. Any other cursor is a client bug and returns nothing.
        q, cursor = params["query"], params["cursorMark"]
        tag = q.split(":")[0]
        if cursor == "*":
            ids, nxt = range(0, per_page), f"{q}|2"
        elif cursor == f"{q}|2":
            ids, nxt = range(per_page, 2 * per_page), f"{q}|3"
        else:
            ids, nxt = [], cursor
        with lock:
            first_call = (q, cursor) not in calls
            calls.append((q, cursor))
        if cursor == "*" and first_call:
            try:
                barrier.wait()      # both searches read page 1 together
            except threading.BrokenBarrierError:
                pass
        resp = MagicMock(status_code=200, ok=True)
        resp.json.return_value = {
            "hitCount": 2 * per_page, "nextCursorMark": nxt,
            "resultList": {"result": [
                {"id": f"{tag}{i}", "doi": f"10.1/{tag}{i}", "title": f"{tag} {i}"}
                for i in ids]},
        }
        return resp

    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config, orch._crossref, orch._unpaywall = {}, None, None
    orch._search_adapters = {"europepmc": EuropePmcAdapter()}

    results = {}

    def run(term):
        # Each query differs, so a shared cursor would be reset between pages.
        fd = {"days_back": 7, "text_groups": [{"title": term}]}
        with patch("requests.Session.get", fake_get):
            recs = orch.search(fd, {"all": True, "selected": []}, max_results=1000)
        results[term] = sorted(r.doi for r in recs)

    from unittest.mock import patch
    threads = [threading.Thread(target=run, args=(t,)) for t in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    # A shared cursor made one search re-read page 1 after the other's page 1.
    for q in {c[0] for c in calls}:
        assert calls.count((q, "*")) == 1, [c for c in calls if c[0] == q]
    for term in ("alpha", "beta"):
        got = results.get(term)
        assert got is not None and len(got) == 2 * per_page, (term, len(got or []))
        assert len(set(got)) == len(got)


def test_b6_adapters_are_fresh_per_search():
    """Every real adapter class gives the orchestrator a new instance per search."""
    from src.sources.europepmc import EuropePmcAdapter
    from src.sources.pubmed import PubMedAdapter
    from src.sources.psyarxiv import PsyArxivAdapter
    from src.sources.socarxiv import SocArxivAdapter
    from src.sources.arxiv import ArxivAdapter
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter

    cfg = {"contact_email": "t@example.org"}
    for cls in (EuropePmcAdapter, PubMedAdapter, PsyArxivAdapter, SocArxivAdapter,
                ArxivAdapter, BiorxivMedrxivAdapter):
        a = cls(sources_config=cfg)
        b = a.for_search()
        assert type(b) is cls and b is not a, cls
        assert b.sources_config == cfg, cls


# ── M31: an enabled source with no adapter is not silently ignored ───────────

def test_m31_enabled_source_without_adapter_warns():
    """sources_config.yaml can enable openalex, which has no search adapter.
    Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M31"""
    cfg = {"publication_sources": {
        "europepmc": {"enabled": True}, "openalex": {"enabled": True},
        "crossref": {"enabled": False}, "unpaywall": {"enabled": False}},
        "contact_email": "t@example.org"}
    orch = SourceOrchestrator(cfg)
    assert "openalex" not in orch.get_enabled_sources()
    assert any("OpenAlex" in w and "no search adapter" in w for w in orch.warnings), orch.warnings


# ── M20: records a source sent but we could not read are counted ─────────────

class _BadRecordsAdapter(_PagedAdapter):
    def __init__(self, total, bad_every):
        super().__init__("europepmc", total)
        self.bad_every = bad_every

    def normalize(self, raw):
        if raw["i"] % self.bad_every == 0:
            raise KeyError("authorList")          # a schema change at the source
        return super().normalize(raw)


def test_m20_normalize_failures_reported(caplog):
    """Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M20"""
    import logging
    statuses = []
    orch = _orch_with({"europepmc": _BadRecordsAdapter(total=10, bad_every=5)})
    with caplog.at_level(logging.WARNING, logger="src.sources.orchestrator"):
        recs = orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                           {"all": True, "selected": []}, on_status=statuses.append)
    assert len(recs) == 8
    assert "Europe PMC: 2 of 10 records could not be read" in statuses
    assert not any("— skipped" in s for s in statuses)
    assert any("could not be read" in r.message for r in caplog.records)


def test_m20_all_unreadable_is_a_source_failure():
    """Adversarial: "0 fetched" must not look like a quiet week."""
    statuses = []
    orch = _orch_with({"europepmc": _BadRecordsAdapter(total=10, bad_every=1)})
    orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                {"all": True, "selected": []}, on_status=statuses.append)
    assert "Europe PMC: 10 of 10 records could not be read" in statuses
    assert "Europe PMC — skipped (error)" in statuses



# ── S2: run preconditions live in the engine; failures are structured ─────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#S2

def test_s2_empty_filter_refused_in_engine():
    from src.sources.orchestrator import EmptyFilterError
    adapter = MagicMock()
    orch = _orch_with({"europepmc": adapter})
    for empty in ({"days_back": 7, "text_groups": []},
                  {"text_groups": [{"both": "   "}]},
                  {"text_groups": [{"keywords": ""}]}):
        with pytest.raises(EmptyFilterError):
            orch.search(empty, {"all": True, "selected": []})
    adapter.search.assert_not_called()


def test_s2_legacy_filter_normalised_before_querying():
    """A legacy {"keywords": ...} group reaches the query builder as a term."""
    seen = []
    adapter = _PagedAdapter("europepmc", total=0)
    real = adapter.search
    adapter.search = lambda q, **kw: seen.append(q) or real(q, **kw)
    _orch_with({"europepmc": adapter}).search(
        {"text_groups": [{"keywords": "zebrafish"}], "date_from": "2020-01-01"},
        {"all": True, "selected": []})
    assert "zebrafish" in seen[0] and "2020-01-01" in seen[0]


def test_s2_failures_structured_not_parsed():
    from src.sources.errors import SourceUnavailableError
    down = MagicMock()
    down.search.side_effect = SourceUnavailableError("down")
    failures, statuses = [], []
    orch = _orch_with({"europepmc": _PagedAdapter("europepmc", total=1000),
                       "biorxiv_medrxiv": down})
    orch.search({"text_groups": [{"both": "x"}]}, {"all": True, "selected": []},
                max_results=200, on_status=statuses.append,
                on_source_failure=lambda n, k: failures.append((n, k)))
    assert failures == [("europepmc", "truncated"), ("biorxiv_medrxiv", "unavailable")]
    # The line people read uses the one label map.
    assert "bioRxiv / medRxiv — skipped (unavailable)" in statuses


def test_b7_orchestrator_reads_a_30_per_page_source_to_its_budget():
    """Browser run 2026-09-29, end to end: a source that sends 30 per page
    (below the orchestrator's 50) is read until the budget, and the cut is
    reported with the source's total instead of passing for a complete read."""
    import tests.test_adapters as ta
    adapter, _ = ta._api_with_pages({"biorxiv": 400, "medrxiv": 200})
    adapter.for_search = lambda: adapter
    statuses = []
    orch = _orch_with({"biorxiv_medrxiv": adapter})
    records = orch.search({"days_back": 7, "text_groups": [{"both": "biorxiv"}]},
                          {"all": True, "selected": []}, max_results=250,
                          on_status=statuses.append)
    # Only bioRxiv titles match "biorxiv"; the budget counts matches (br3).
    assert "bioRxiv / medRxiv — skipped (truncated)" in statuses, statuses
    assert "bioRxiv / medRxiv: 470 of 600 papers read, 270 match the filter" in statuses, statuses
    assert len(records) == 270                    # well past the first page of each server
    # Read to the end when the budget allows it: not reported as truncated.
    adapter2, _ = ta._api_with_pages({"biorxiv": 95, "medrxiv": 40})
    adapter2.for_search = lambda: adapter2
    statuses2 = []
    _orch_with({"biorxiv_medrxiv": adapter2}).search(
        {"days_back": 7, "text_groups": [{"both": "rxiv"}]}, {"all": True, "selected": []},
        max_results=1000, on_status=statuses2.append)
    assert not any("truncated" in s for s in statuses2), statuses2



# ── bioRxiv/medRxiv: the budget counts matches, a page limit bounds reading ──
# Decision 2026-09-29 (docs/cycles/2026-09-29_browser-run.md): the API cannot
# search words, so the first papers of the window used the whole budget.

def _sparse_biorxiv(n_bio, n_med, every=50, max_pages=None):
    import tests.test_adapters as ta
    adapter, calls = ta._api_with_pages(
        {"biorxiv": n_bio, "medrxiv": n_med},
        title=lambda server, i: (f"needle study {server} {i}" if i % every == 0
                                 else f"other {server} {i}"))
    adapter.for_search = lambda: adapter
    orch = _orch_with({"biorxiv_medrxiv": adapter})
    from src.sources.config import load_sources_config
    shipped = load_sources_config()["publication_sources"]["biorxiv_medrxiv"]["max_pages"]
    orch.config = {"publication_sources": {"biorxiv_medrxiv": {
        "max_pages": shipped if max_pages is None else max_pages}}}
    return orch, calls


def test_br3_biorxiv_budget_counts_matches():
    """Real scale: two weeks is ~4,900 papers. 1 in 50 matches; a budget of
    200 must not stop after the first 200 papers read."""
    orch, calls = _sparse_biorxiv(3400, 1500)                # the shipped page limit
    assert orch._page_limit("biorxiv_medrxiv") >= 115        # two weeks of bioRxiv fits
    statuses, failures = [], []
    records = orch.search({"days_back": 14, "text_groups": [{"both": "needle"}]},
                          {"all": True, "selected": []}, max_results=200,
                          on_status=statuses.append,
                          on_source_failure=lambda *a: failures.append(a))
    assert len(records) == 68 + 30                    # every match in the window
    assert "bioRxiv / medRxiv: 4,900 of 4,900 papers read, 98 match the filter" in statuses, statuses
    assert failures == []                             # read to the end: not truncated


def test_br3_page_limit_from_config():
    orch, calls = _sparse_biorxiv(3400, 1500, max_pages=5)
    assert orch._page_limit("biorxiv_medrxiv") == 5
    statuses, failures = [], []
    records = orch.search({"days_back": 14, "text_groups": [{"both": "needle"}]},
                          {"all": True, "selected": []}, max_results=200,
                          on_status=statuses.append,
                          on_source_failure=lambda *a: failures.append(a))
    assert len([c for c in calls if c[0] == "biorxiv"]) == 5
    assert failures == [("biorxiv_medrxiv", "page-limit")]
    assert "bioRxiv / medRxiv: 300 of 4,900 read (page limit reached)" in statuses, statuses
    assert len(records) == 6                          # the matches in the pages read


def test_br3_other_sources_still_count_what_they_read():
    """Europe PMC searches the words itself; its budget is unchanged."""
    big = _PagedAdapter("europepmc", total=1000)
    orch = _orch_with({"europepmc": big})
    records = orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                          {"all": True, "selected": []}, max_results=200)
    assert len(records) == 200


def test_br3_page_limit_bad_config_falls_back(caplog):
    orch = _orch_with({})
    orch.config = {"publication_sources": {"biorxiv_medrxiv": {"max_pages": "lots"}}}
    assert orch._page_limit("biorxiv_medrxiv") == orch.MAX_PAGES_PER_SOURCE
    assert "not a number" in caplog.text


def test_br3_page_limit_message_is_configured():
    from src.sources.config import load_sources_config
    assert "page limit" in load_sources_config()["failure_explanations"]["page-limit"]



def test_br3_progress_counts_matches_and_status_shows_papers_read():
    """QA gate 2026-09-29 F2: the progress count mixed papers read (bioRxiv)
    with matches (other sources)."""
    orch, _ = _sparse_biorxiv(300, 0)
    progress, statuses = [], []
    orch.search({"days_back": 14, "text_groups": [{"both": "needle"}]},
                {"all": True, "selected": []}, max_results=200,
                on_progress=lambda f, t: progress.append((f, t)),
                on_status=statuses.append)
    assert max(f for f, _t in progress) == 6            # matches, never papers read
    assert "bioRxiv / medRxiv: 300 of 300 papers read, 6 match so far…" in statuses, statuses


def test_br10_every_paper_read_is_accounted_for(caplog):
    """Production run 2026-09-29: "read 5,095 … 149 match (4,924 did not)"
    left 22 papers unexplained — matches that repeat a paper already read
    (the details API lists each version) were merged and not counted."""
    import logging
    import tests.test_adapters as ta
    # 300 papers; every 10th is "needle", and every 30th repeats the DOI of
    # the needle before it (a new version in the same window).
    adapter, _ = ta._api_with_pages(
        {"biorxiv": 300},
        title=lambda server, i: f"needle study {i}" if i % 10 == 0 else f"other {i}",
        doi=lambda server, i: f"10.1101/b{i - 10 if i % 30 == 0 and i else i}")
    adapter.for_search = lambda: adapter
    orch = _orch_with({"biorxiv_medrxiv": adapter})
    statuses = []
    with caplog.at_level(logging.INFO, logger="src.sources.orchestrator"):
        records = orch.search({"days_back": 14, "text_groups": [{"both": "needle"}]},
                              {"all": True, "selected": []}, max_results=200,
                              on_status=statuses.append)
    assert len(records) == 21                     # 30 needles, 9 of them repeats
    assert ("bioRxiv / medRxiv: 300 of 300 papers read, 21 match the filter "
            "(9 more were repeats of a paper already read)") in statuses, statuses
    assert "21 match the filter, 270 do not, 9 repeat a paper already read, " \
           "0 could not be read" in caplog.text
    assert "accounted for" not in caplog.text


# ── BW3: a range bioRxiv/medRxiv is not read directly is not a failure ───────
# docs/implementation_plan_2026-10-07_biorxiv_window.md

def test_bw3_skip_is_not_a_failure():
    """The reported case: 2019–2020 used to read 150 pages per server from
    January 2019 and end in "page limit reached". Now: no requests, no failure."""
    from datetime import date
    from unittest.mock import patch
    orch, calls = _sparse_biorxiv(3400, 1500, max_pages=150)
    statuses, failures = [], []
    with patch("src.sources.biorxiv_medrxiv._today", return_value=date(2026, 10, 7)):
        records = orch.search({"days_back": 0, "start_date": "2019-01-01",
                               "end_date": "2020-12-31", "text_groups": [{"both": "needle"}]},
                              {"all": True, "selected": []}, max_results=200,
                              on_status=statuses.append,
                              on_source_failure=lambda *a: failures.append(a))
    assert calls == [] and records == []
    assert failures == []
    assert not any("page limit" in s for s in statuses), statuses


# ── DS: say when a source's papers were already found ────────────────────────
# docs/implementation_plan_2026-10-07_duplicate_status.md

def test_ds1_fetched_status():
    from src.sources.orchestrator import fetched_status
    assert fetched_status("PubMed", 52, 0) == "PubMed: 52 fetched"
    assert fetched_status("PubMed", 0, 52) == \
        "PubMed: 52 papers read, all already found by an earlier source"
    assert fetched_status("PubMed", 3, 49) == "PubMed: 3 new, 49 already found by an earlier source"
    assert fetched_status("PubMed", 0, 0) == "PubMed: 0 fetched"
    assert fetched_status("PubMed", 1234, 5678) == \
        "PubMed: 1,234 new, 5,678 already found by an earlier source"
    # DS gate F2: a source's own repeats are not "an earlier source".
    assert fetched_status("PubMed", 10, 0, 3) == "PubMed: 10 new, 3 repeated within PubMed"
    assert fetched_status("PubMed", 0, 4, 2) == \
        "PubMed: 0 new, 4 already found by an earlier source, 2 repeated within PubMed"


class _SharedDoiAdapter(_PagedAdapter):
    """Papers with DOIs shared across sources, labelled with this source."""

    def normalize(self, raw):
        return _make_record(doi=f"10.1/shared{raw['i']}", source=self.prefix,
                            title=f"shared paper {raw['i']}")


def test_ds2_overlap_is_named():
    """Two sources returning the same papers (Europe PMC includes PubMed)."""
    # Same DOIs, each source's own label — as Europe PMC and PubMed records are.
    def run(second_total):
        statuses = []
        records = _orch_with({"europepmc": _SharedDoiAdapter("europepmc", total=30),
                              "pubmed": _SharedDoiAdapter("pubmed", total=second_total)}).search(
            {"days_back": 7, "text_groups": [{"both": "x"}]}, {"all": True, "selected": []},
            max_results=200, on_status=statuses.append)
        return records, statuses
    records, statuses = run(30)
    assert len(records) == 30
    assert "Europe PMC: 30 fetched" in statuses, statuses
    assert "PubMed: 30 papers read, all already found by an earlier source" in statuses, statuses
    records, statuses = run(40)
    assert len(records) == 40
    assert "PubMed: 10 new, 30 already found by an earlier source" in statuses, statuses



class _RepeatingAdapter(_PagedAdapter):
    """Sends each of its papers twice in one page (a source's own repeats)."""

    def search(self, query, page=1, page_size=50, **_):
        recs = super().search(query, page, page_size)
        return recs + recs


def test_ds2_own_repeats_are_not_an_earlier_source():
    """DS gate F2, adversarial (P7): repeats inside one source must not be
    reported as found by an earlier source."""
    statuses = []
    _orch_with({"pubmed": _RepeatingAdapter("solo", total=10)}).search(
        {"days_back": 7, "text_groups": [{"both": "x"}]}, {"all": True, "selected": []},
        max_results=200, on_status=statuses.append)
    assert "PubMed: 10 new, 10 repeated within PubMed" in statuses, statuses
    assert not any("earlier source" in s for s in statuses), statuses


def test_ds2_counts_are_not_shared_between_concurrent_searches():
    """DS gate F1 / TD10: the orchestrator is shared and runs searches two at
    a time. Two searches run in parallel threads, each pausing mid-search so
    they overlap, each with a different overlap; each gets its own line, and
    no search leaves state on the orchestrator."""
    import threading
    gate = threading.Barrier(2, timeout=5)

    class _Paused(_SharedDoiAdapter):
        def search(self, query, page=1, page_size=50, **_):
            if page == 1:
                gate.wait()            # both searches are inside a source now
            return super().search(query, page, page_size)

    orch = _orch_with({"europepmc": _SharedDoiAdapter("europepmc", total=30),
                       "pubmed": _Paused("pubmed", total=30)})
    before = set(vars(orch))
    out = {}

    def run(name, selected):
        st = []
        orch.search({"days_back": 7, "text_groups": [{"both": "x"}]},
                    {"all": False, "selected": selected}, max_results=200,
                    on_status=st.append)
        out[name] = [s for s in st if s.startswith("PubMed:") and "reading" not in s]

    a = threading.Thread(target=run, args=("both", ["europepmc", "pubmed"]))
    b = threading.Thread(target=run, args=("alone", ["pubmed"]))
    a.start(); b.start(); a.join(10); b.join(10)
    assert out["both"] == ["PubMed: 30 papers read, all already found by an earlier source"], out
    assert out["alone"] == ["PubMed: 30 fetched"], out
    assert set(vars(orch)) == before, set(vars(orch)) - before


def test_ds3_local_filter_source_keeps_one_final_line():
    """DS gate F3: bioRxiv/medRxiv posts its own fuller line; no second one."""
    orch, _ = _sparse_biorxiv(60, 30, every=10)
    statuses = []
    orch.search({"days_back": 7, "text_groups": [{"both": "needle"}]},
                {"all": True, "selected": []}, max_results=200, on_status=statuses.append)
    finals = [s for s in statuses if s.startswith("bioRxiv / medRxiv:")
              and ("fetched" in s or "match the filter" in s)]
    assert finals == ["bioRxiv / medRxiv: 90 of 90 papers read, 9 match the filter"], finals


def test_td8_old_name_still_works():
    orch = _orch_with({"europepmc": object(), "pubmed": object()})
    sel = {"all": False, "selected": ["pubmed"]}
    assert orch.resolve_active_sources(sel) == orch._resolve_active_sources(sel) == ["pubmed"]


# ── TD9/TD10 (docs/implementation_plan_2026-10-07_gate_todos.md) ─────────────

def test_td9_resend_after_merge_is_a_repeat():
    """Europe PMC sends X; PubMed sends X twice: 1 already found, 1 repeated."""
    class _TwiceAdapter(_SharedDoiAdapter):
        def search(self, query, page=1, page_size=50, **_):
            recs = super().search(query, page, page_size)
            return recs + recs
    statuses = []
    _orch_with({"europepmc": _SharedDoiAdapter("europepmc", total=1),
                "pubmed": _TwiceAdapter("pubmed", total=1)}).search(
        {"days_back": 7, "text_groups": [{"both": "x"}]}, {"all": True, "selected": []},
        max_results=200, on_status=statuses.append)
    assert ("PubMed: 0 new, 1 already found by an earlier source, 1 repeated within PubMed"
            in statuses), statuses
