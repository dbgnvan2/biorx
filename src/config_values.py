"""
Numbers read from the YAML config files, defensively.

Purpose: A blank, mistyped, fractional, infinite or out-of-range setting falls
         back to its default with a warning naming the key; it never makes a
         search, summary or Discover run fail.
Spec:    docs/implementation_plan_2026-10-08_gate_todos.md#TG1, #TG2, #TG6
Tests:   tests/test_config_values.py
"""

from __future__ import annotations

import logging
import math
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _blank(block: Optional[Mapping], key: str, value: Any, default: Any, name: str) -> bool:
    if value is None or value == "":
        if key in (block or {}):
            logger.warning("config %s is blank — using %s", name, default)
        return True
    return False


def config_int(block: Optional[Mapping], key: str, default: int, minimum: int = 1,
               name: str = "") -> int:
    """A whole number >= minimum from block[key], else the default (logged).
    Refused: blank, text, a fraction, a bool, inf/nan, below the minimum.
    `name` is the setting's full path for the warning (default: key)."""
    name = name or key
    value = (block or {}).get(key)
    if _blank(block, key, value, default, name):
        return default
    try:
        if isinstance(value, bool):
            raise ValueError
        n = int(value)
        if n != float(value) or n < minimum:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        logger.warning("config %s is not a whole number of at least %d (%r) — using %d",
                       name, minimum, value, default)
        return default
    return n


def config_float(block: Optional[Mapping], key: str, default: float,
                 minimum: float = 0.0, name: str = "") -> float:
    """A finite number >= minimum from block[key], else the default (logged)."""
    name = name or key
    value = (block or {}).get(key)
    if _blank(block, key, value, default, name):
        return default
    try:
        if isinstance(value, bool):
            raise ValueError
        x = float(value)
        if not math.isfinite(x) or x < minimum:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        logger.warning("config %s is not a number of at least %s (%r) — using %s",
                       name, minimum, value, default)
        return default
    return x
