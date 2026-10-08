"""
How many papers a search reads from each source.

Purpose: One default, one maximum, and one rule for a saved filter's own
         limit, for the web app and the monitor alike.
Spec:    docs/implementation_plan_2026-10-08_limits_and_warnings.md#FL1, #FL4, #FL5
Tests:   tests/web/test_searches_routes.py::test_fl5_limits_from_config,
         tests/test_monitor.py::test_fl4_*
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

_FALLBACK = (200, 2000)


def search_limits(sources_config: Optional[Mapping]) -> Tuple[int, int]:
    """(default, ceiling) from sources_config.yaml search:."""
    block = (sources_config or {}).get("search") or {}
    if "default_max_results" not in block or "max_results_ceiling" not in block:
        logger.warning("sources_config has no search.default_max_results / "
                       "max_results_ceiling — using 200 and 2000")
    return (int(block.get("default_max_results", _FALLBACK[0])),
            int(block.get("max_results_ceiling", _FALLBACK[1])))


def clean_limit(value: Any, ceiling: int) -> Optional[int]:
    """A limit as an int within 1..ceiling; None when not set. ValueError
    otherwise (a bool, a fraction, text, out of range)."""
    if value is None or value == "":
        return None
    try:
        n = int(value)
        if isinstance(value, bool) or n != float(value):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"not a whole number: {value!r}") from None
    if not 1 <= n <= ceiling:
        raise ValueError(f"{n} is outside 1..{ceiling}")
    return n


def filter_limit(filter_dict: Mapping, sources_config: Optional[Mapping]) -> int:
    """A saved filter's own limit, else the default; an unusable stored value
    is logged and the default used."""
    default, ceiling = search_limits(sources_config)
    try:
        return clean_limit((filter_dict or {}).get("max_results"), ceiling) or default
    except ValueError as e:
        logger.warning("Filter has an unusable max_results (%s); using %d", e, default)
        return default
