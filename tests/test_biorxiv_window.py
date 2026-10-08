"""
Which part of a range bioRxiv/medRxiv is read directly.

Spec:  docs/implementation_plan_2026-10-07_biorxiv_window.md#BW1
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.sources.biorxiv_window import (FULL, RECENT, SKIP, biorxiv_direct_window,
                                        window_settings)

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize("start, end, mode, wstart, wend", [
    ("2026-09-23", "2026-10-07", FULL, "2026-09-23", "2026-10-07"),     # 15 days
    ("2026-09-17", "2026-10-07", FULL, "2026-09-17", "2026-10-07"),     # exactly 21
    ("2026-09-16", "2026-10-07", RECENT, "2026-09-17", "2026-10-07"),   # 22 → last 21
    ("2019-01-01", "2026-10-07", RECENT, "2026-09-17", "2026-10-07"),   # the reported case
    ("2026-07-20", "2026-08-08", FULL, "2026-07-20", "2026-08-08"),     # ends exactly 60 days ago
    ("2026-07-19", "2026-08-07", SKIP, "", ""),                          # ends 61 days ago
    ("2019-01-01", "2020-12-31", SKIP, "", ""),
])
def test_bw1_window(start, end, mode, wstart, wend):
    w = biorxiv_direct_window(start, end, TODAY, 21, 60)
    assert (w.mode, w.start, w.end) == (mode, wstart, wend)
    assert (w.note == "") == (mode == FULL)


def test_bw1_notes_say_where_the_papers_come_from():
    recent = biorxiv_direct_window("2019-01-01", "2026-10-07", TODAY, 21, 60)
    assert "last 21 days" in recent.note and "through Europe PMC" in recent.note
    skip = biorxiv_direct_window("2019-01-01", "2020-12-31", TODAY, 21, 60)
    assert "not read directly" in skip.note and "through Europe PMC" in skip.note
    assert "Tick Europe PMC" not in skip.note
    unticked = biorxiv_direct_window("2019-01-01", "2020-12-31", TODAY, 21, 60,
                                     europepmc_selected=False)
    assert unticked.note.endswith("Tick Europe PMC to include them.")


def test_bw7_settings_from_config(caplog):
    cfg = {"publication_sources": {"biorxiv_medrxiv": {"max_direct_days": 14,
                                                       "europepmc_lag_days": 30}}}
    assert window_settings(cfg) == (14, 30)
    with caplog.at_level("WARNING"):
        assert window_settings({}) == (21, 60)
    assert any("max_direct_days" in r.getMessage() for r in caplog.records)


def test_bw7_repo_config_has_the_keys():
    from src.sources.config import load_sources_config
    cfg = load_sources_config()
    section = cfg["publication_sources"]["biorxiv_medrxiv"]
    assert section["max_direct_days"] > 0 and section["europepmc_lag_days"] > 0


def test_bw5_notes():
    from src.sources.biorxiv_window import biorxiv_notes
    cfg = {"publication_sources": {"biorxiv_medrxiv": {
        "max_direct_days": 21, "europepmc_lag_days": 60, "max_pages": 150}}}
    long_old = {"days_back": 0, "start_date": "2019-01-01", "end_date": "2020-12-31"}
    assert biorxiv_notes(long_old, ["europepmc"], cfg, TODAY) == []          # not searched
    [note] = biorxiv_notes(long_old, ["europepmc", "biorxiv_medrxiv"], cfg, TODAY)
    assert "not read directly" in note and "Tick" not in note
    [note] = biorxiv_notes(long_old, ["biorxiv_medrxiv"], cfg, TODAY)
    assert note.endswith("Tick Europe PMC to include them.")
    [note] = biorxiv_notes({"days_back": 0, "start_date": "2019-01-01", "end_date": "2026-10-07"},
                           ["europepmc", "biorxiv_medrxiv"], cfg, TODAY)
    assert "last 21 days" in note
    [note] = biorxiv_notes({"days_back": 0, "start_date": "2026-09-30", "end_date": "2026-10-07"},
                           ["biorxiv_medrxiv"], cfg, TODAY)
    assert "every paper in the range is read" in note and "150 pages per server" in note
