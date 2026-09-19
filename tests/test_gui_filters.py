"""
Tests for E1 — Saved Filters in the Search panel.

Spec: docs/implementation_plan_2026-06-07.md#E1

These exercise the pure check-state / enabled helpers extracted from the GUI so
they run without constructing a MainWindow. A headless QListWidget check (under
QT_QPA_PLATFORM=offscreen) verifies the helper is actually applied to rows.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# PyQt6 must be importable for these tests; skip cleanly if it is not.
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem

import gui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def test_e1_1_filter_initial_check_state_is_unchecked():
    """E1.1: the startup check state for a saved-filter row is always Unchecked."""
    assert gui.filter_initial_check_state() == Qt.CheckState.Unchecked


def test_e1_1_filters_unchecked_on_startup(qapp):
    """E1.1: rows populated with the helper are unchecked even when enabled=True."""
    filters = [
        {"name": "A", "enabled": True},
        {"name": "B", "enabled": False},
        {"name": "C"},  # enabled field missing -> defaults to enabled
    ]
    widget = QListWidget()
    for f in filters:
        item = QListWidgetItem(f["name"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(gui.filter_initial_check_state())
        item.setData(Qt.ItemDataRole.UserRole, f)
        widget.addItem(item)

    assert widget.count() == 3
    for i in range(widget.count()):
        assert widget.item(i).checkState() == Qt.CheckState.Unchecked


def test_e1_2_run_all_enabled_uses_enabled_field():
    """E1.2: 'enabled' field (not check state) decides Run All Enabled membership."""
    assert gui.filter_is_enabled({"name": "A", "enabled": True}) is True
    assert gui.filter_is_enabled({"name": "B", "enabled": False}) is False
    # Missing field defaults to enabled, preserving prior behaviour.
    assert gui.filter_is_enabled({"name": "C"}) is True


def test_fr2_5_gui_search_enriches_only_matched_papers():
    """FR2.5 (plan 2026-09-18 C2): the desktop SearchWorker passes enrich_only
    through, so the real orchestrator skips papers the filter dropped."""
    from unittest.mock import MagicMock
    from tests.test_orchestrator import _make_record
    from src.sources.orchestrator import SourceOrchestrator

    keep = _make_record(doi="10.1/keep", title="Generative agents")
    drop = _make_record(doi="10.1/drop", title="Protein folding")
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    adapter = MagicMock()
    adapter.search.return_value = [{}, {}]
    adapter.normalize.side_effect = [keep, drop]
    adapter.last_page_size = 2
    adapter.last_total = 2
    orch._search_adapters = {"europepmc": adapter}
    orch._crossref = MagicMock()
    orch._unpaywall = None

    worker = gui.SearchWorker(
        orch, {"text_groups": [{"both": "generative"}], "authors": []}, save_to_db=False,
    )
    worker.run()

    assert [c.args[0] for c in orch._crossref.enrich.call_args_list] == [keep]


def test_i4_gui_rows_get_enriched_fields():
    """Issue 4: the desktop worker streamed rows before enrichment and saved
    those copies. After the search it now emits (streamed, enriched) pairs,
    and what it saves carries the enriched fields."""
    from unittest.mock import MagicMock
    from tests.test_orchestrator import _make_record
    from src.sources.orchestrator import SourceOrchestrator

    rec = _make_record(doi="10.1/keep", title="Generative agents")
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    adapter = MagicMock()
    adapter.search.return_value = [{}]
    adapter.normalize.side_effect = [rec]
    adapter.last_page_size = 1
    adapter.last_total = 1
    orch._search_adapters = {"europepmc": adapter}
    orch._crossref = None

    def unpaywall_enrich(record):
        record.pdf_url = "https://oa.example/keep.pdf"
        return True
    orch._unpaywall = MagicMock()
    orch._unpaywall.enrich.side_effect = unpaywall_enrich

    db = MagicMock()
    worker = gui.SearchWorker(orch, {"text_groups": [{"both": "generative"}]},
                              save_to_db=True, db=db)
    streamed, refreshed = [], []
    worker.batch_ready.connect(streamed.extend)
    worker.refreshed.connect(refreshed.extend)
    worker.run()

    assert streamed and streamed[0]["pdf_url"] == ""          # streamed before enrichment
    (key, fresh), = refreshed
    assert key == gui.paper_key(streamed[0]) and fresh["pdf_url"] == "https://oa.example/keep.pdf"
    assert db.insert_paper.call_args.args[0]["pdf_url"] == "https://oa.example/keep.pdf"


def test_i4_apply_enrichment_updates_rows_in_place():
    """The GUI-thread slot finds each row by its paper key and updates the dict
    the table and selection hold; a row not in the table is left alone."""
    from unittest.mock import MagicMock
    row = {"title": "T", "doi": "10.1/a", "canonical_id": "doi:10.1/a", "pdf_url": ""}
    other = {"title": "U", "doi": "10.1/b", "canonical_id": "doi:10.1/b", "pdf_url": ""}
    tab = MagicMock()
    tab.current_results = [row, other]
    gui.SearchBrowseTab._apply_enrichment(
        tab, [(gui.paper_key(dict(row)), {**row, "pdf_url": "u"}),
              ("doi:10.1/zzz", {"pdf_url": "stray"})])
    assert row["pdf_url"] == "u" and other["pdf_url"] == ""
    tab.display_page.assert_called_once()
