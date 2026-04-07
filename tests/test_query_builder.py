"""Tests for query_builder.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.sources.query_builder import build_europepmc_query, build_psyarxiv_query, _species_clause, _ANIMAL_ORGANISM_EXCLUSIONS


def _q(text_groups=None, days_back=7, keywords=None):
    return {"text_groups": text_groups or [], "days_back": days_back, "keywords": keywords}


# ── Europe PMC query builder ──────────────────────────────────────────────────

def test_date_only_no_text_groups():
    q = build_europepmc_query({"text_groups": [], "days_back": 7})
    assert "FIRST_PDATE" in q
    assert "TO" in q


def test_single_both_term():
    q = build_europepmc_query(_q([{"title": "", "abstract": "", "both": "maternal"}]))
    assert "maternal" in q
    assert "FIRST_PDATE" in q


def test_single_title_term():
    q = build_europepmc_query(_q([{"title": "stress", "abstract": "", "both": ""}]))
    assert "TITLE:stress" in q


def test_single_abstract_term():
    q = build_europepmc_query(_q([{"title": "", "abstract": "cortisol", "both": ""}]))
    assert "ABSTRACT:cortisol" in q


def test_multiple_terms_in_field_produces_or():
    q = build_europepmc_query(_q([{"title": "stress,cortisol", "abstract": "", "both": ""}]))
    assert "OR" in q
    assert "TITLE:stress" in q
    assert "TITLE:cortisol" in q


def test_multiple_groups_produces_or_between_groups():
    groups = [
        {"title": "stress", "abstract": "", "both": ""},
        {"title": "", "abstract": "", "both": "maternal"},
    ]
    q = build_europepmc_query(_q(groups))
    assert "stress" in q
    assert "maternal" in q
    assert " OR " in q


def test_wildcard_passes_through():
    q = build_europepmc_query(_q([{"title": "", "abstract": "", "both": "inflam*"}]))
    assert "inflam*" in q


def test_and_between_different_fields():
    q = build_europepmc_query(_q([{"title": "stress", "abstract": "", "both": "maternal"}]))
    assert "AND" in q
    assert "TITLE:stress" in q
    assert "maternal" in q


def test_keywords_fallback():
    q = build_europepmc_query({"text_groups": [], "days_back": 7, "keywords": ["maternal", "stress"]})
    assert "maternal" in q
    assert "stress" in q


# ── PsyArXiv query builder ────────────────────────────────────────────────────

def test_psyarxiv_plain_keywords():
    q = build_psyarxiv_query(_q([{"title": "", "abstract": "", "both": "attachment, emotion"}]))
    assert "attachment" in q
    assert "emotion" in q


def test_psyarxiv_strips_wildcards():
    q = build_psyarxiv_query(_q([{"title": "inflam*", "abstract": "", "both": ""}]))
    assert "inflam" in q
    assert "*" not in q


def test_psyarxiv_deduplicates_terms():
    groups = [
        {"title": "stress", "abstract": "stress", "both": ""},
    ]
    q = build_psyarxiv_query(_q(groups))
    # "stress" should appear only once
    assert q.count("stress") == 1


# ── Species / study-type clause ───────────────────────────────────────────────

def test_species_any_produces_no_clause():
    assert _species_clause("(any)") == ""


def test_species_human_only_excludes_common_model_organisms():
    clause = _species_clause("Human studies only")
    assert "NOT ANIMAL:y" in clause
    assert 'NOT ORGANISM:"Mus musculus"' in clause
    assert 'NOT ORGANISM:"Rattus norvegicus"' in clause


def test_species_exclude_animal_same_as_human_only():
    assert _species_clause("Exclude animal studies") == _species_clause("Human studies only")


def test_species_exclude_animal_uses_full_exclusion_list():
    clause = _species_clause("Exclude animal studies")
    assert clause == _ANIMAL_ORGANISM_EXCLUSIONS


def test_species_animal_only():
    assert _species_clause("Animal studies only") == "ANIMAL:y"


def test_species_clause_appended_to_query():
    q = build_europepmc_query({
        "text_groups": [{"title": "", "abstract": "", "both": "inflammation"}],
        "days_back": 7,
        "species": "Human studies only",
    })
    assert "NOT ANIMAL:y" in q
    assert "inflammation" in q


def test_species_clause_date_only_query():
    """Species filter should also apply to date-only (no text) queries."""
    q = build_europepmc_query({"text_groups": [], "days_back": 7, "species": "Animal studies only"})
    assert "ANIMAL:y" in q
    assert "FIRST_PDATE" in q


def test_species_any_not_in_query():
    """'(any)' must add no organism clause to the query."""
    q = build_europepmc_query({
        "text_groups": [{"title": "", "abstract": "", "both": "stress"}],
        "days_back": 7,
        "species": "(any)",
    })
    assert "ANIMAL" not in q
    assert "ORGANISM" not in q


# ── Date range override ───────────────────────────────────────────────────────

def test_explicit_date_range_overrides_days_back():
    q = build_europepmc_query({
        "text_groups": [{"title": "", "abstract": "", "both": "stress"}],
        "days_back": 7,
        "start_date": "2024-01-01",
        "end_date":   "2024-06-30",
    })
    assert "2024-01-01" in q
    assert "2024-06-30" in q


def test_days_back_used_when_no_explicit_dates():
    from datetime import datetime, timedelta
    q = build_europepmc_query({"text_groups": [], "days_back": 30})
    expected_start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    assert expected_start in q
