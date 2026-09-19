"""
Tests for agents/monitor.py — the headless CLI front end.

Review finding 4: the CLI is a second front end, and must reproduce the GUI's
client-side filtering or the same saved filter means different things depending
on which surface ran it.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))

from src.sources.schema import (
    CanonicalRecord, AuthorRecord, SourceHit, RecordFlags,
)
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "monitor", Path(__file__).parent.parent / "agents" / "monitor.py"
)
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)


def _record(title, abstract="", author="Park J", version="1"):
    return CanonicalRecord(
        canonical_id=f"arxiv:{abs(hash(title))}", title=title, abstract=abstract,
        authors=[AuthorRecord(display_name=author, sequence=1)],
        year=2024, published_date="2024-01-01", document_type="preprint",
        is_preprint=True, journal_or_server="arXiv", doi="", pmid="", pmcid="",
        source_url="https://arxiv.org/abs/x", best_oa_url="", pdf_url="",
        license="", oa_status="open", subjects=[], keywords=[],
        source_hits=[SourceHit(source="arxiv", source_record_id="x",
                               fetched_at="2024-01-01")],
        flags=RecordFlags(), source_trust_weight=0.75,
    )


def test_run_search_applies_the_filter_to_source_results():
    """
    A source query is approximate; records that don't match the saved filter
    must not reach stdout.
    """
    orch = MagicMock()
    orch.search.return_value = [
        _record("Generative Agents in Simulation", "personas and emotion"),
        _record("Protein Folding Kinetics", "unrelated to the filter"),
    ]
    filter_dict = {
        "text_groups": [{"title": "", "abstract": "", "both": "generative agents"}],
        "authors": [],
    }

    out = monitor.run_search(orch, filter_dict, "test")

    assert [p["title"] for p in out] == ["Generative Agents in Simulation"]


def test_run_search_returns_plain_dicts_ready_for_json():
    orch = MagicMock()
    orch.search.return_value = [_record("Generative Agents", "x")]
    out = monitor.run_search(orch, {"text_groups": [], "authors": ["Park"]}, "test")

    assert out and isinstance(out[0], dict)
    assert out[0]["title"] == "Generative Agents"


def test_run_search_survives_a_filter_with_authors():
    """Regression: `authors` is a list in filters.json, not a string."""
    orch = MagicMock()
    orch.search.return_value = [_record("Generative Agents", "x", author="Park J")]

    out = monitor.run_search(orch, {"text_groups": [], "authors": ["Park"]}, "test")
    assert len(out) == 1

    out = monitor.run_search(orch, {"text_groups": [], "authors": ["Nobody"]}, "test")
    assert out == []


def test_cli_and_gui_agree_on_the_same_filter_and_records():
    """
    The two front ends must return the same set — asserted by comparing the
    CLI's output with a direct call to the shared implementation.
    """
    from src.filtering import filter_papers

    records = [
        _record("Generative Agents in Simulation", "personas"),
        _record("Protein Folding Kinetics", "unrelated"),
    ]
    filter_dict = {
        "text_groups": [{"title": "", "abstract": "", "both": "generative agents"}],
        "authors": [],
    }

    orch = MagicMock()
    orch.search.return_value = records

    cli_out = monitor.run_search(orch, filter_dict, "test")
    gui_out = filter_papers([r.to_dict() for r in records], filter_dict)

    assert [p["canonical_id"] for p in cli_out] == [p["canonical_id"] for p in gui_out]


def test_fr1_6_empty_filter_is_skipped(capsys):
    """FR1.6: a scheduled run of an empty filter searches nothing and says so."""
    orch = MagicMock()
    orch.search.return_value = [_record("Generative Agents", "x")]
    out = monitor.run_search(orch, {"text_groups": [{"both": "  "}], "authors": []}, "empty")

    assert out == []
    orch.search.assert_not_called()
    assert "[empty] Skipped: This filter has no search terms" in capsys.readouterr().err
