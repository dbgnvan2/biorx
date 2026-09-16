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


def filter_has_text(f: Dict[str, Any]) -> bool:
    """Return True if the filter has at least one non-empty text search term."""
    groups = f.get("text_groups", [])
    for g in groups:
        if any(g.get(k, "").strip() for k in TEXT_FIELDS):
            return True
    if any(f.get(k) for k in ("authors", "institution")):
        return True
    return False
