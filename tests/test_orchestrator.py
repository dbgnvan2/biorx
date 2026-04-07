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
