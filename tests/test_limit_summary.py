"""
The "results are incomplete" summary.

Spec:  docs/implementation_plan_2026-10-08_limits_and_warnings.md#WS3, #WS4, #WS6
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.limit_summary import limit_summary
from src.sources.config import load_sources_config

CFG = load_sources_config()
ALL = ["europepmc", "pubmed", "psyarxiv", "socarxiv", "biorxiv_medrxiv", "arxiv"]
BOTH_ONLY = {"days_back": 90, "text_groups": [{"title": "", "abstract": "", "both": "cooperation"}]}
WITH_TITLE = {"days_back": 90, "text_groups": [{"title": "cooperation", "abstract": "", "both": ""}]}


def _summary(failures, limits, active=ALL, fd=BOTH_ONLY, limit=200, ceiling=2000, cfg=CFG):
    return limit_summary(failures, limits, active, fd, limit, ceiling, cfg)


def test_ws3_the_owners_case():
    failures = {"europepmc": "truncated", "pubmed": "truncated", "psyarxiv": "truncated",
                "socarxiv": "truncated", "biorxiv_medrxiv": "partial", "arxiv": "truncated"}
    limits = {"europepmc": {"read": 200, "total": 2391}, "pubmed": {"read": 200, "total": 1204},
              "psyarxiv": {"read": 200, "total": 5310}, "socarxiv": {"read": 200, "total": 1877},
              "arxiv": {"read": 200, "total": 913}}
    s = _summary(failures, limits)
    assert s["heading"] == "Results are incomplete — 6 sources could not be read in full."
    assert s["next_step"] == ("Most useful next step: put your main word in the Title box "
                              "(PsyArXiv and SocArXiv read every paper in the date range "
                              "without one), and untick PubMed (it is part of Europe PMC).")
    rows = {r["source"]: r for r in s["rows"]}
    assert rows["europepmc"]["counts"] == "read 200 of 2,391 matches"
    assert rows["europepmc"]["action"] == "Too many to read (2,391): narrow the words (add AND …, or use the Title box)."
    assert rows["pubmed"]["action"] == "Part of Europe PMC — untick it."
    assert rows["psyarxiv"]["counts"] == "read 200 of 5,310 papers in the date range"
    assert rows["psyarxiv"]["action"].startswith("No Title word")
    assert rows["biorxiv_medrxiv"]["counts"] == "stopped responding part-way; results from it are incomplete"
    assert rows["biorxiv_medrxiv"]["action"] == "Its preprints come through Europe PMC — untick it."
    assert rows["arxiv"]["action"] == "Raise the limit to 913 or less, or narrow the words."
    assert rows["arxiv"]["label"] == "arXiv"
    assert s["limit"] == 200


def test_ws3_nothing_failed_is_no_summary():
    assert _summary({}, {}) is None


def test_ws3_one_source_wording():
    s = _summary({"arxiv": "truncated"}, {"arxiv": {"read": 200, "total": 450}}, active=["arxiv"])
    assert s["heading"] == "Results are incomplete — 1 source could not be read in full."
    assert s["next_step"] == "Most useful next step: raise the limit (papers read per source)."


def test_ws3_total_not_reported():
    s = _summary({"arxiv": "truncated"}, {"arxiv": {"read": 200, "total": None}}, active=["arxiv"])
    assert s["rows"][0]["counts"] == "read 200 matches (total not reported)"
    assert s["rows"][0]["action"] == "Raise the limit, or narrow the words."


# ── WS4: advice must not misfire (P7) ─────────────────────────────────────────

def test_ws4_pubmed_without_europe_pmc_is_not_told_to_untick():
    s = _summary({"pubmed": "truncated"}, {"pubmed": {"read": 200, "total": 900}},
                 active=["pubmed"])
    assert "untick" not in s["rows"][0]["action"]
    assert s["rows"][0]["action"] == "Raise the limit to 900 or less, or narrow the words."


def test_ws4_osf_with_a_title_word_is_not_told_to_add_one():
    s = _summary({"psyarxiv": "truncated"}, {"psyarxiv": {"read": 200, "total": 640}},
                 active=["psyarxiv"], fd=WITH_TITLE)
    assert "Title" not in s["rows"][0]["action"]
    assert s["rows"][0]["counts"] == "read 200 of 640 papers with your Title words"


def test_ws4_over_the_ceiling_never_suggests_raising():
    s = _summary({"europepmc": "truncated"}, {"europepmc": {"read": 2000, "total": 25411}},
                 active=["europepmc"], limit=2000)
    assert "Raise" not in s["rows"][0]["action"] and "raise" not in s["next_step"]
    s = _summary({"europepmc": "truncated"}, {"europepmc": {"read": 200, "total": 2000}},
                 active=["europepmc"])
    assert s["rows"][0]["action"].startswith("Raise the limit to 2,000")      # boundary: fits


def test_ws4_osf_group_without_title_anywhere_counts_as_not_narrowed():
    fd = {"days_back": 90, "text_groups": [{"title": "x", "abstract": "", "both": ""},
                                           {"title": "", "abstract": "", "both": "y"}]}
    s = _summary({"socarxiv": "truncated"}, {"socarxiv": {"read": 200, "total": 3000}},
                 active=["socarxiv"], fd=fd)
    assert s["rows"][0]["action"].startswith("No Title word")


# ── WS6: wording from config ─────────────────────────────────────────────────

def test_ws6_wording_comes_from_config():
    cfg = {**CFG, "limit_summary": {"next_step_order": ["raise_limit"],
                                    "actions": {"raise_limit": "CUSTOM {total:,} / {limit}"},
                                    "next_step": {"raise_limit": "do the custom thing"}}}
    s = _summary({"arxiv": "truncated"}, {"arxiv": {"read": 200, "total": 1500}},
                 active=["arxiv"], cfg=cfg)
    assert s["rows"][0]["action"] == "CUSTOM 1,500 / 200"
    assert s["next_step"] == "Most useful next step: do the custom thing."


def test_ws6_missing_config_warns_and_falls_back(caplog):
    cfg = {k: v for k, v in CFG.items() if k != "limit_summary"}
    with caplog.at_level("WARNING"):
        s = _summary({"arxiv": "truncated"}, {"arxiv": {"read": 200, "total": 300}},
                     active=["arxiv"], cfg=cfg)
    assert s["rows"][0]["action"].startswith("Raise the limit to 300")
    assert any("limit_summary" in r.getMessage() for r in caplog.records)


def test_ws6_repo_config_has_every_action_and_step():
    block = CFG["limit_summary"]
    keys = set(block["next_step_order"])
    assert keys <= set(block["actions"]) and keys <= set(block["next_step"])
