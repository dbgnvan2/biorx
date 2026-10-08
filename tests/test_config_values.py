"""The defensive config reader (TG1, TG2, TG6).

Spec: docs/implementation_plan_2026-10-08_gate_todos.md
"""

import logging

import pytest

from src.config_values import config_float, config_int

BAD_INTS = [None, "", "lots", 2.5, True, False, 0, -3, float("inf"), float("-inf"),
            float("nan"), [1], {"a": 1}]


@pytest.mark.parametrize("bad", BAD_INTS)
def test_tg6_config_int_never_raises(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert config_int({"k": bad}, "k", 7, name="sec.k") == 7
    assert any("sec.k" in r.getMessage() for r in caplog.records)


def test_tg6_config_int_reads_good_values():
    assert config_int({"k": 5}, "k", 7) == 5
    assert config_int({"k": "5"}, "k", 7) == 5
    assert config_int({"k": 5.0}, "k", 7) == 5
    assert config_int({"k": 0}, "k", 7, minimum=0) == 0
    assert config_int({}, "k", 7) == 7 and config_int(None, "k", 7) == 7


@pytest.mark.parametrize("bad", [None, "", "x", True, -1, float("inf"), float("nan"), [1]])
def test_tg6_config_float_never_raises(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert config_float({"k": bad}, "k", 1.5, name="sec.k") == 1.5
    assert any("sec.k" in r.getMessage() for r in caplog.records)


def test_tg6_config_float_reads_good_values():
    assert config_float({"k": 0.25}, "k", 1.0) == 0.25
    assert config_float({"k": "3"}, "k", 1.0) == 3.0
    assert config_float({"k": 0.05}, "k", 1.0, minimum=0.1) == 1.0


def test_tg6_every_numeric_setting_survives_a_bad_value(monkeypatch):
    """Each caller falls back instead of raising when its setting is inf."""
    from src import fulltext, llm_config, paper_meta
    from src.sources import config as sources_config_mod
    from src.sources.biorxiv_window import window_settings
    from src.sources.orchestrator import SourceOrchestrator
    inf = float("inf")
    orch = SourceOrchestrator({"publication_sources": {"arxiv": {"max_pages": inf}}})
    assert orch._page_limit("arxiv") == orch.MAX_PAGES_PER_SOURCE
    assert window_settings({"publication_sources": {"biorxiv_medrxiv": {
        "max_direct_days": inf, "europepmc_lag_days": inf}}}) == (21, 60)
    bad_ft = {"full_text": {"max_pages": inf, "extract_timeout_seconds": inf,
                            "extract_memory_mb": inf, "scrape_max_chars": inf}}
    monkeypatch.setattr(sources_config_mod, "load_sources_config", lambda *a, **k: bad_ft)
    limits = fulltext.extract_limits()
    assert limits["max_pages"] == 40 and limits["timeout"] == 60.0
    assert limits["mem_bytes"] == 1024 * 1024 * 1024
    assert paper_meta.scrape_max_chars() == paper_meta.SCRAPE_MAX_CHARS
    assert llm_config.max_text_chars({"max_text_chars": inf}) == \
        llm_config._FALLBACK["max_text_chars"]
    assert llm_config.summary_daily_cap({"summary_daily_cap_per_user": inf}) == \
        llm_config._FALLBACK["summary_daily_cap_per_user"]
    assert llm_config.summary_daily_cap({"summary_daily_cap_per_user": 0}) == 0


def test_tg6_discover_settings_survive_bad_values():
    from src.discover import discover_settings
    s = discover_settings({"discover": {"days_back": float("inf"), "max_papers": "",
                                        "check_timeout_s": "x", "check_delay_s": -1}})
    assert (s.days_back, s.max_papers, s.check_timeout_s, s.check_delay_s) == (90, 30, 15.0, 0.2)
