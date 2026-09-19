"""
Purpose: Read and write saved filters, and answer pure questions about one.
Spec:    docs/implementation_plan_2026-09-15.md#2.1
Tests:   tests/test_filters_store.py

The desktop app keeps filters in filters.json; the web app will keep them per
user in SQLite (added in a later phase). Both read them through this module so
a saved filter means the same thing on every surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

PathLike = Union[str, Path]

TEXT_FIELDS = ("title", "abstract", "both")


def load_filters_file(path: PathLike) -> List[Dict[str, Any]]:
    """Return the filters in a filters.json file, or [] if it does not exist."""
    p = Path(path)
    if p.exists():
        with open(p) as f:
            return json.load(f).get("filters", [])
    return []


def save_filters_file(path: PathLike, filters: List[Dict[str, Any]]) -> None:
    """Write filters back to a filters.json file."""
    with open(Path(path), "w") as f:
        json.dump({"filters": filters}, f, indent=2)


def filter_is_enabled(f: Dict[str, Any]) -> bool:
    """Purpose: Report whether a filter participates in 'Run All Enabled'.
    Spec:    docs/implementation_plan_2026-06-07.md#E1.2
    Tests:   tests/test_gui_filters.py::test_e1_2_run_all_enabled_uses_enabled_field

    This reads the persisted ``enabled`` flag and is independent of the row's
    visual check state in the Search panel.
    """
    return f.get("enabled", True)


# Shown wherever a run is refused because the filter has nothing to search for.
EMPTY_FILTER_MESSAGE = (
    "This filter has no search terms, authors or institution — "
    "add at least one before running it."
)


def filter_has_text(f: Dict[str, Any]) -> bool:
    """Purpose: Say whether a filter has anything to search for.
    Spec:    docs/implementation_plan_2026-09-18_filter_run.md#FR1
    Tests:   tests/test_filters_store.py::test_filter_has_text,
             tests/test_filters_store.py::test_fr1_4_legacy_keywords_group_has_criteria,
             tests/test_filters_store.py::test_fr1_5_blank_values_are_empty

    True when the filter has a non-blank text term, author or institution.
    Without one, every source returns its whole date window unfiltered, so
    every entry point (GUI, web routes, monitor) refuses to run it. Read after
    normalise_filter so a legacy web-saved {"keywords": ...} group counts, as
    does a top-level "keywords" field — but only when text_groups is empty,
    the one case where filter_papers and the query builders read it.
    """
    from src.filtering import normalise_filter, normalize_authors

    f = normalise_filter(f)
    for g in f.get("text_groups") or []:
        if any(str(g.get(k) or "").strip() for k in TEXT_FIELDS):
            return True
    # Top-level "keywords" counts only where its readers use it: filter_papers
    # and the query builders fall back to it only when text_groups is empty.
    if not f.get("text_groups"):
        kw = f.get("keywords")
        if isinstance(kw, (list, tuple)) and any(str(k).strip() for k in kw):
            return True
    if normalize_authors(f.get("authors")):
        return True
    return bool(str(f.get("institution") or "").strip())
