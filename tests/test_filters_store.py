"""
Tests for src/filters_store.py — saved-filter persistence and pure predicates.

Spec: docs/implementation_plan_2026-09-15.md#2.1 (Phase 0 extraction)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.filters_store import (
    filter_has_text, filter_is_enabled, load_filters_file, save_filters_file,
)

REPO_ROOT = Path(__file__).parent.parent


def test_load_returns_empty_list_when_the_file_is_absent(tmp_path):
    assert load_filters_file(tmp_path / "nope.json") == []


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "filters.json"
    filters = [{"name": "A", "enabled": True}, {"name": "B", "enabled": False}]
    save_filters_file(path, filters)
    assert load_filters_file(path) == filters
    # written in the shape the GUI expects
    assert json.loads(path.read_text())["filters"] == filters


def test_load_reads_the_real_filters_json():
    """The live file is the contract, not a hand-built fixture."""
    filters = load_filters_file(REPO_ROOT / "filters.json")
    assert filters and all("name" in f for f in filters)


@pytest.mark.parametrize("f,expected", [
    ({"enabled": True}, True),
    ({"enabled": False}, False),
    ({}, True),                      # absent flag means enabled
])
def test_filter_is_enabled(f, expected):
    assert filter_is_enabled(f) is expected


@pytest.mark.parametrize("f,expected", [
    ({"text_groups": [{"title": "", "abstract": "", "both": "agent"}]}, True),
    ({"text_groups": [{"title": "stress", "abstract": "", "both": ""}]}, True),
    ({"text_groups": [{"title": "", "abstract": "", "both": "   "}]}, False),
    ({"text_groups": []}, False),
    ({"text_groups": [], "authors": ["Park"]}, True),
    ({"text_groups": [], "institution": "Stanford"}, True),
    ({"text_groups": [], "authors": [], "institution": ""}, False),
])
def test_filter_has_text(f, expected):
    assert filter_has_text(f) is expected


def test_gui_uses_the_shared_store():
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    assert gui.load_filters is load_filters_file
    assert gui.save_filters is save_filters_file
    assert gui.filter_is_enabled is filter_is_enabled
    assert gui._filter_has_text is filter_has_text


def test_fr1_4_legacy_keywords_group_has_criteria():
    """FR1.4: an earlier web build saved groups as {"keywords": ...}. That looks
    empty to a check that reads only title/abstract/both, but it has a term."""
    assert filter_has_text({"text_groups": [{"keywords": "inflammation"}]}) is True
    assert filter_has_text({"keywords": "biology"}) is True
    assert filter_has_text({"keywords": ["biology"]}) is True


@pytest.mark.parametrize("f", [
    {"text_groups": [{"title": " ", "abstract": "\t", "both": ""}]},
    {"text_groups": [], "authors": [""]},
    {"text_groups": [], "authors": ["  ", ","]},
    {"text_groups": [], "institution": "   "},
    {"text_groups": [], "institution": []},
    {"text_groups": [{"keywords": ""}]},
    {"keywords": "  "},
    {"keywords": ["", " "]},
    {"text_groups": [{"title": None, "abstract": None, "both": None}]},
    {"category": "neuroscience", "days_back": 7, "text_groups": []},
    # Adversarial: keywords present but ignored by every reader, because
    # text_groups is non-empty. Looks like a term; searches nothing.
    {"text_groups": [{"both": ""}], "keywords": ["stress"]},
])
def test_fr1_5_blank_values_are_empty(f):
    """FR1.5: blank strings, blank author entries and category/date alone are
    not something to search for."""
    assert filter_has_text(f) is False


def test_fr1_7_keyword_string_is_a_term_list_not_letters():
    """FR1 review finding: a string keywords field was split into letters by
    filter_papers, so "stress" matched any paper containing an "s"."""
    from src.filtering import filter_papers, normalise_filter
    assert normalise_filter({"keywords": "stress, cortisol"})["keywords"] == ["stress", "cortisol"]
    papers = [{"title": "Soil carbon"}, {"title": "Stress in rats"}]
    assert [p["title"] for p in filter_papers(papers, {"keywords": "stress"})] == ["Stress in rats"]
