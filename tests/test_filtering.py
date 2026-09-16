"""
Tests for src/filtering.py — the single implementation of a saved filter's
client-side semantics, shared by the GUI and the headless CLI.

Covers review findings 1 (authors type contract) and 4 (CLI must filter).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.filtering import filter_papers, normalize_authors, text_group_matches

REPO_ROOT = Path(__file__).parent.parent


def _paper(**kw):
    base = {
        "title": "Generative Agents", "abstract": "A study of personas.",
        "authors": "Park J; Smith A", "author_corresponding": "Park J",
        "author_corresponding_institution": "Stanford",
        "type": "preprint", "version": "1", "published": "NA", "license": "cc_by",
    }
    base.update(kw)
    return base


# ── normalize_authors: the shape contract both readers must agree on ──────────

def test_normalize_authors_accepts_the_gui_list_shape():
    assert normalize_authors(["Smith", "Jones"]) == ["Smith", "Jones"]


def test_normalize_authors_accepts_a_comma_separated_string():
    """A string must not be iterated character-by-character."""
    assert normalize_authors("Smith, Jones") == ["Smith", "Jones"]


def test_normalize_authors_splits_commas_inside_list_entries():
    assert normalize_authors(["Smith, Jones", "Lee"]) == ["Smith", "Jones", "Lee"]


@pytest.mark.parametrize("empty", [None, "", [], (), 0, {"a": 1}])
def test_normalize_authors_empty_and_unsupported_shapes_yield_no_terms(empty):
    assert normalize_authors(empty) == []


# ── filter_papers ─────────────────────────────────────────────────────────────

def test_filter_papers_author_filter_as_string_does_not_match_every_paper():
    """
    Regression: `[a for a in f["authors"]]` over a string yields single
    characters, and 'p' is in almost every author list — so a string-shaped
    author filter silently matched everything.
    """
    papers = [_paper(authors="Zylstra Q", author_corresponding="Zylstra Q")]
    assert filter_papers(papers, {"authors": "Park"}) == []
    assert len(filter_papers(papers, {"authors": "Zylstra"})) == 1


def test_filter_papers_author_filter_as_list_matches():
    papers = [_paper()]
    assert len(filter_papers(papers, {"authors": ["Park"]})) == 1
    assert filter_papers(papers, {"authors": ["Nobody"]}) == []


def test_filter_papers_applies_text_groups():
    papers = [_paper(title="Generative Agents"), _paper(title="Protein Folding", abstract="x")]
    groups = [{"title": "", "abstract": "", "both": "generative agents"}]
    out = filter_papers(papers, {"text_groups": groups, "authors": []})
    assert [p["title"] for p in out] == ["Generative Agents"]


def test_filter_papers_applies_non_text_facets():
    """paper_type/version/published/license have no query-time equivalent."""
    papers = [_paper(version="1"), _paper(version="3", title="Revised")]
    out = filter_papers(papers, {"version": "2+ (revised only)"})
    assert [p["title"] for p in out] == ["Revised"]


def test_text_group_matches_supports_prefix_wildcard():
    assert text_group_matches(_paper(title="Adolescent stress"),
                              {"title": "adolescen*", "abstract": "", "both": ""})
    assert not text_group_matches(_paper(title="Adult stress"),
                                  {"title": "adolescen*", "abstract": "", "both": ""})


# ── Real artifact, not a hand-built dict ──────────────────────────────────────

def test_every_saved_filter_in_filters_json_is_readable_by_the_filter():
    """
    The shapes in the live filters.json are the contract. Hand-built fixtures
    are what let the `authors`-as-string assumption survive review.
    """
    data = json.loads((REPO_ROOT / "filters.json").read_text())
    saved = data["filters"] if isinstance(data, dict) and "filters" in data else data
    assert saved, "filters.json has no filters to check against"

    papers = [_paper()]
    for f in saved:
        filter_papers(papers, f)          # must not raise on any real filter
        normalize_authors(f.get("authors"))


def test_gui_uses_the_shared_implementation():
    """
    The GUI must not keep a private copy — two implementations of one predicate
    drift. Skipped where PyQt6 is unavailable.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    assert gui._filter_papers is filter_papers
    assert gui._text_group_matches is text_group_matches
