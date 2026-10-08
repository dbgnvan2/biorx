"""All years start date and the date-window hint from config.

Spec: docs/implementation_plan_2026-10-08_date_window.md#AY3, #DW4
"""

import logging

import pytest

from src.search_limits import all_years_start, date_window_hint
from src.sources.config import load_sources_config


def test_ay3_start_from_config():
    assert all_years_start(load_sources_config()) == "1900-01-01"
    assert all_years_start({"search": {"all_years_start": "1950-06-01"}}) == "1950-06-01"


@pytest.mark.parametrize("cfg", [None, {}, {"search": {}}, {"search": {"all_years_start": ""}},
                                 {"search": {"all_years_start": "long ago"}},
                                 {"search": {"all_years_start": 1900}}])
def test_ay3_missing_or_bad_start_warns_and_falls_back(cfg, caplog):
    with caplog.at_level(logging.WARNING):
        assert all_years_start(cfg) == "1900-01-01"
    assert any("all_years_start" in r.getMessage() for r in caplog.records)


def test_dw4_config_hint():
    assert date_window_hint(load_sources_config()).startswith("Dates are when a paper first appeared")
    assert date_window_hint({"date_window": {"hint": " Own words. "}}) == "Own words."


@pytest.mark.parametrize("cfg", [None, {}, {"date_window": {}}, {"date_window": {"hint": "  "}}])
def test_dw4_missing_key_falls_back(cfg, caplog):
    with caplog.at_level(logging.WARNING):
        assert date_window_hint(cfg).startswith("Dates are when a paper first appeared")
    assert any("date_window.hint" in r.getMessage() for r in caplog.records)
