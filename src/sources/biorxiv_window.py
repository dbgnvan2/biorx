"""
Which part of a date range to read from bioRxiv/medRxiv directly.

Purpose: Read bioRxiv/medRxiv directly only where it adds papers Europe PMC
         may not have yet; one rule for the adapter and the message shown.
Spec:    docs/implementation_plan_2026-10-07_biorxiv_window.md#BW1
Tests:   tests/test_biorxiv_window.py

bioRxiv's API cannot search words and lists papers oldest first, so a long
range used to read its first few weeks and stop at the page limit. Europe PMC
indexes bioRxiv and medRxiv preprints with word search, but lags on the
newest ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

FULL, RECENT, SKIP = "full", "recent", "skip"


@dataclass(frozen=True)
class DirectWindow:
    mode: str              # FULL, RECENT or SKIP
    start: str             # YYYY-MM-DD; "" when SKIP
    end: str
    note: str              # "" when FULL


def _day(s: str) -> date:
    return date.fromisoformat(s[:10])


def biorxiv_direct_window(start: str, end: str, today: date, max_days: int,
                          lag_days: int, europepmc_selected: bool = True) -> DirectWindow:
    """The part of [start, end] to read directly, and what to tell the user.

    - ends more than lag_days before today: SKIP (Europe PMC has these);
    - longer than max_days: RECENT, the last max_days of the range;
    - otherwise FULL, the whole range.
    """
    s, e = _day(start), _day(end)
    tail = "" if europepmc_selected else " Tick Europe PMC to include them."
    if e < today - timedelta(days=lag_days):
        return DirectWindow(SKIP, "", "", (
            "bioRxiv/medRxiv: not read directly for this range (its API lists "
            "papers oldest first and cannot search words); their preprints come "
            "through Europe PMC." + tail))
    if (e - s).days + 1 > max_days:
        clipped = e - timedelta(days=max_days - 1)
        return DirectWindow(RECENT, clipped.isoformat(), e.isoformat(), (
            f"bioRxiv/medRxiv: reading only the last {max_days} days of the range "
            f"directly; earlier bioRxiv/medRxiv preprints come through Europe PMC." + tail))
    return DirectWindow(FULL, s.isoformat(), e.isoformat(), "")


def window_settings(sources_config: Optional[dict]) -> tuple:
    """(max_direct_days, europepmc_lag_days) from sources_config.yaml, with
    the defaults the plan names (21, 60) when a key is missing."""
    import logging
    section = ((sources_config or {}).get("publication_sources") or {}).get("biorxiv_medrxiv") or {}
    if "max_direct_days" not in section or "europepmc_lag_days" not in section:
        logging.getLogger(__name__).warning(
            "sources_config biorxiv_medrxiv has no max_direct_days / europepmc_lag_days "
            "— using 21 and 60")
    return int(section.get("max_direct_days", 21)), int(section.get("europepmc_lag_days", 60))


def biorxiv_notes(filter_dict: dict, active_sources, sources_config: Optional[dict],
                  today: date) -> list:
    """What to tell the user about the bioRxiv/medRxiv direct read for this
    search: [] when it is not among the sources searched.

    Purpose: The note on screen comes from the same rule the adapter follows.
    Spec:    docs/implementation_plan_2026-10-07_biorxiv_window.md#BW5
    Tests:   tests/web/test_searches_routes.py::test_bw5_long_range_note_on_the_job,
             tests/test_biorxiv_window.py::test_bw5_notes
    """
    active = list(active_sources or [])
    if "biorxiv_medrxiv" not in active:
        return []
    from .query_builder import get_date_range
    start, end = get_date_range(filter_dict or {})
    max_days, lag_days = window_settings(sources_config)
    w = biorxiv_direct_window(start, end, today, max_days, lag_days,
                              europepmc_selected="europepmc" in active)
    if w.mode != FULL:
        return [w.note]
    section = ((sources_config or {}).get("publication_sources") or {}).get("biorxiv_medrxiv") or {}
    pages = section.get("max_pages")
    limit = f"; it stops after {pages} pages per server" if pages else ""
    return [f"bioRxiv/medRxiv: its API cannot search words, so every paper in the "
            f"range is read and checked{limit}."]
