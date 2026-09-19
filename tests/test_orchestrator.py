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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
        filter_dict={"days_back": 7, "text_groups": []},
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
    assert problems == [("Crossref", 2, 2)]
    assert "Crossref failed for 2 of 2 papers" in messages
    assert any("Crossref lookups failed for 2 of 2" in r.message for r in caplog.records)


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
