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



# ── TG1/TG2/TG4: numeric settings read defensively; fallbacks match config ───
# docs/implementation_plan_2026-10-08_gate_todos.md

@pytest.mark.parametrize("bad", [None, "", "lots", 2.5, True, 0, -3, float("inf"),
                                 float("-inf"), float("nan")])
def test_tg1_bad_values_fall_back(bad, caplog):
    from src.search_limits import search_limits
    with caplog.at_level(logging.WARNING):
        assert search_limits({"search": {"default_max_results": bad,
                                         "max_results_ceiling": 2000}}) == (200, 2000)
        assert search_limits({"search": {"default_max_results": 200,
                                         "max_results_ceiling": bad}}) == (200, 2000)
    assert any("default_max_results" in r.getMessage() for r in caplog.records)
    assert any("max_results_ceiling" in r.getMessage() for r in caplog.records)


def test_tg1_good_values_and_text_numbers_are_read():
    from src.search_limits import search_limits
    assert search_limits({"search": {"default_max_results": "50",
                                     "max_results_ceiling": 400.0}}) == (50, 400)


def test_tg1_default_above_ceiling_is_clamped(caplog):
    """Gate F3: a lowered ceiling is kept; the default comes down to it."""
    from src.search_limits import search_limits
    with caplog.at_level(logging.WARNING):
        assert search_limits({"search": {"default_max_results": 900,
                                         "max_results_ceiling": 500}}) == (500, 500)
        assert search_limits({"search": {"default_max_results": 200,
                                         "max_results_ceiling": 100}}) == (100, 100)
    assert any("above" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("bad", [None, "", "x", 2.5, True, 0, float("inf"), 3])
def test_tg2_osf_terms_summary_and_adapter_agree(bad):
    """Gate F4: the limit summary and the OSF adapter read
    osf.max_title_terms with one reader, so they agree on every value."""
    from src.limit_summary import _osf_narrowed
    from src.sources.osf import DEFAULT_MAX_TITLE_TERMS
    from src.sources.psyarxiv import PsyArxivAdapter
    from src.sources.query_builder import osf_title_terms
    groups = [{"title": f"word{i}"} for i in range(DEFAULT_MAX_TITLE_TERMS)]
    for cfg in ({}, {"osf": {}}, {"osf": {"max_title_terms": bad}}):
        adapter_max = PsyArxivAdapter(sources_config=cfg)._max_title_terms()
        for fd in ({"days_back": 7, "text_groups": groups},
                   {"days_back": 7, "text_groups": groups + [{"title": "one more"}]}):
            assert _osf_narrowed(fd, cfg) == bool(osf_title_terms(fd, adapter_max)), (cfg, fd)


def test_tg4_fallbacks_match_the_shipped_config():
    """The frozen defaults equal sources_config.yaml, so they cannot drift."""
    from src import search_limits as sl
    from src.sources.biorxiv_window import window_settings
    from src.sources.osf import DEFAULT_MAX_TITLE_TERMS
    cfg = load_sources_config()
    assert sl.search_limits(cfg) == sl._FALLBACK
    assert sl.all_years_start(cfg) == sl._ALL_YEARS_FALLBACK
    assert sl.date_window_hint(cfg) == sl._HINT_FALLBACK
    assert window_settings(cfg) == window_settings({})
    assert int((cfg.get("osf") or {}).get("max_title_terms")) == DEFAULT_MAX_TITLE_TERMS
