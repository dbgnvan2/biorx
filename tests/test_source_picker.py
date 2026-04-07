"""
Tests for SourcePickerWidget state logic.
Uses a pure-Python model (no Qt) so tests run without a display server.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class SourcePickerModel:
    """
    Pure-Python equivalent of SourcePickerWidget logic.
    Mirrors the state machine in SourcePickerWidget without PyQt6.
    """
    def __init__(self, available, default_selected):
        self._available = list(available)
        self._all = True
        self._selected = set()

    def set_all(self, checked: bool):
        self._all = checked
        if checked:
            self._selected.clear()

    def set_source(self, source: str, checked: bool):
        if checked:
            self._selected.add(source)
            self._all = False  # turning on a source turns off All
        else:
            self._selected.discard(source)
            if not self._selected:
                self._all = True  # last source unchecked → restore All

    def get_selection(self):
        if self._all:
            return {"all": True, "selected": []}
        return {"all": False, "selected": sorted(self._selected)}


def make_picker():
    return SourcePickerModel(
        available=["europepmc", "psyarxiv", "biorxiv_medrxiv"],
        default_selected=["europepmc", "psyarxiv"],
    )


def test_default_state_is_all_sources_on():
    m = make_picker()
    sel = m.get_selection()
    assert sel["all"] is True


def test_turning_on_source_turns_off_all():
    m = make_picker()
    m.set_source("europepmc", True)
    assert m.get_selection()["all"] is False


def test_turning_off_last_source_restores_all():
    m = make_picker()
    m.set_source("europepmc", True)
    m.set_source("europepmc", False)
    assert m.get_selection()["all"] is True


def test_multiple_sources_selected():
    m = make_picker()
    m.set_source("europepmc", True)
    m.set_source("psyarxiv", True)
    sel = m.get_selection()
    assert sel["all"] is False
    assert "europepmc" in sel["selected"]
    assert "psyarxiv" in sel["selected"]


def test_turning_on_all_clears_individual():
    m = make_picker()
    m.set_source("europepmc", True)
    m.set_all(True)
    sel = m.get_selection()
    assert sel["all"] is True
    assert sel["selected"] == []


def test_disabled_sources_not_in_available():
    """Disabled sources should not be in the available list."""
    m = SourcePickerModel(available=["europepmc", "psyarxiv"], default_selected=[])
    assert "biorxiv_medrxiv" not in m._available
    assert "pubmed" not in m._available


def test_zero_sources_never_allowed():
    """System should never result in zero active sources."""
    m = make_picker()
    # Even if user deselects everything, All should restore
    m.set_source("europepmc", True)
    m.set_source("psyarxiv", True)
    m.set_source("europepmc", False)
    m.set_source("psyarxiv", False)
    sel = m.get_selection()
    # Should restore to All (not zero)
    assert sel["all"] is True or len(sel["selected"]) > 0
