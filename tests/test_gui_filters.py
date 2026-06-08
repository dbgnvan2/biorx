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
