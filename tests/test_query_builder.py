"""Tests for query_builder.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.sources.query_builder import build_europepmc_query, build_psyarxiv_query, build_arxiv_query, _species_clause, _animal_organism_exclusions


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
    assert clause == _animal_organism_exclusions()
    # The organism list moved to filter_vocabulary.yaml (review S3); the
    # clause sent to Europe PMC must be what it was when it lived in code.
    assert clause == (
        'NOT ANIMAL:y NOT ORGANISM:"Mus musculus" NOT ORGANISM:"Rattus norvegicus" '
        'NOT ORGANISM:Mouse NOT ORGANISM:Rat NOT ORGANISM:Zebrafish '
        'NOT ORGANISM:"Danio rerio" NOT ORGANISM:"Drosophila melanogaster" '
        'NOT ORGANISM:"Caenorhabditis elegans" NOT ORGANISM:"Macaca mulatta"')


def test_s3_species_ids_and_legacy_labels_agree():
    assert _species_clause("no-animal") == _species_clause("Exclude animal studies")
    assert _species_clause("human") == _species_clause("Human studies only")
    assert _species_clause("animal") == "ANIMAL:y"
    assert _species_clause("any") == ""


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


# ── arXiv query builder ───────────────────────────────────────────────────────

def test_arxiv_single_both_term():
    q = build_arxiv_query(_q([{"title": "", "abstract": "", "both": "agent"}]))
    assert "all:agent" in q
    assert "submittedDate:[" in q


def test_arxiv_phrase_quoting():
    q = build_arxiv_query(_q([{"title": "", "abstract": "", "both": "multi-agent systems"}]))
    # Multi-word phrase should be quoted
    assert 'all:"multi-agent systems"' in q


def test_arxiv_title_field():
    q = build_arxiv_query(_q([{"title": "agent", "abstract": "", "both": ""}]))
    assert "ti:agent" in q


def test_arxiv_abstract_field():
    q = build_arxiv_query(_q([{"title": "", "abstract": "LLM", "both": ""}]))
    assert "abs:LLM" in q


def test_arxiv_multiple_terms_in_field_or_joined():
    q = build_arxiv_query(_q([{"title": "", "abstract": "", "both": "agent, simulation"}]))
    assert "OR" in q
    assert "all:agent" in q
    assert "all:simulation" in q


def test_arxiv_multiple_groups_or_joined():
    groups = [
        {"title": "", "abstract": "", "both": "agent"},
        {"title": "", "abstract": "", "both": "simulation"},
    ]
    q = build_arxiv_query(_q(groups))
    # Groups should be OR-joined
    assert " OR " in q
    assert "all:agent" in q
    assert "all:simulation" in q


def test_arxiv_and_between_fields():
    q = build_arxiv_query(_q([{"title": "agent", "abstract": "LLM", "both": ""}]))
    # Within a group, different fields should be AND-joined
    assert "AND" in q
    assert "ti:agent" in q
    assert "abs:LLM" in q


def test_arxiv_date_format():
    q = build_arxiv_query({"text_groups": [], "days_back": 7})
    # Date should be in YYYYMMDDHHmm format
    assert "submittedDate:[" in q
    assert "2359]" in q  # End of day


def test_arxiv_date_only_no_text():
    """Pure date-range query without text should be valid."""
    q = build_arxiv_query({"text_groups": [], "days_back": 7})
    assert "submittedDate:[" in q
    assert "all:" not in q
    assert "ti:" not in q


def test_arxiv_authors_not_in_query(
):
    """
    M3 resolution: arXiv's au:"…" clause is stricter than the client-side
    substring match, so author filtering is done entirely client-side by
    filter_papers(). build_arxiv_query must not include au: clauses regardless
    of whether the filter has authors.
    """
    q = build_arxiv_query({
        "text_groups": [],
        "days_back": 7,
        "authors": ["Smith", "Jones"],
    })
    assert "au:" not in q
    assert "submittedDate:[" in q


def test_arxiv_authors_not_in_query_with_text():
    """Author absence holds when text groups are also present."""
    q = build_arxiv_query({
        "text_groups": [{"title": "", "abstract": "", "both": "agent"}],
        "days_back": 7,
        "authors": ["Smith"],
    })
    assert "au:" not in q
    assert "all:agent" in q


def test_arxiv_ignores_unsupported_filters():
    """species, paper_type, license, published should be ignored for arXiv."""
    q = build_arxiv_query({
        "text_groups": [{"title": "", "abstract": "", "both": "agent"}],
        "days_back": 7,
        "species": "Human studies only",
        "paper_type": "article",
        "license": "CC-BY",
        "published": "2025-01-01",
    })
    # Should not have species/paper_type/license restrictions
    assert "ANIMAL" not in q
    assert "paper_type" not in q
    assert "CC-BY" not in q
    # But should have the text and date
    assert "all:agent" in q
    assert "submittedDate:[" in q


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


def test_arxiv_authors_accepts_the_legacy_string_shape():
    """
    Hand-written filters may still store authors as one comma-separated string.
    The query must build without raising (authors are filtered client-side, not
    at query time — M3 decision).
    """
    q = build_arxiv_query({"text_groups": [], "days_back": 7, "authors": "Smith, Jones"})
    assert "submittedDate:[" in q
    assert "au:" not in q


def test_arxiv_query_builds_for_every_saved_filter():
    """
    Built against the real filters.json, not a hand-made dict: the `authors`
    field is a list there, and a string-shaped assumption crashed the builder.
    """
    import json
    from pathlib import Path as _P

    # The seed file always (it has an authors list); the owner's own
    # filters.json too when this machine has one (review M32).
    root = _P(__file__).parent.parent
    saved = []
    for name in ("filters.seed.json", "filters.json"):
        if (root / name).exists():
            data = json.loads((root / name).read_text())
            saved += data["filters"] if isinstance(data, dict) and "filters" in data else data
    assert saved, "no saved filters to check against"
    assert any(f.get("authors") for f in saved), "no filter with an authors list"

    for f in saved:
        q = build_arxiv_query(f)           # must not raise on any real filter
        assert "submittedDate:[" in q


# ── B2: empty-string dates mean "not set" ─────────────────────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B2
# Saved filters and the web editor store "" for an unused date. Before the fix
# get_date_range returned "" as the date, arXiv got submittedDate:[0000 TO 2359]
# (HTTP 500 live) and the OSF sources got an empty date_created filter (HTTP 400).

def _empty_date_filter(days_back=7):
    return {"text_groups": [{"title": "inflammation", "abstract": "", "both": ""}],
            "days_back": days_back, "start_date": "", "end_date": ""}


def test_b2_empty_string_dates_use_days_back_arxiv():
    from datetime import datetime, timedelta
    q = build_arxiv_query(_empty_date_filter(7))
    start = (datetime.today() - timedelta(days=7)).strftime("%Y%m%d")
    end = datetime.today().strftime("%Y%m%d")
    assert f"submittedDate:[{start}0000 TO {end}2359]" in q
    assert "[0000 TO" not in q


def test_b2_empty_string_dates_use_days_back_osf():
    from datetime import datetime, timedelta
    from unittest.mock import MagicMock
    from src.sources.psyarxiv import PsyArxivAdapter

    adapter = PsyArxivAdapter()
    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = {"data": []}
    adapter.session.get = MagicMock(return_value=resp)

    adapter.search("inflammation", filter_dict=_empty_date_filter(7))

    params = adapter.session.get.call_args.kwargs["params"]
    assert params["filter[date_created][gte]"] == \
        (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    assert params["filter[date_created][lte]"] == datetime.today().strftime("%Y-%m-%d")


def test_b2_none_dates_treated_as_missing():
    from src.sources.query_builder import get_date_range
    start, end = get_date_range({"days_back": 3, "start_date": None, "end_date": None})
    assert start and end and start < end


# ── AND3–AND5: "a AND b" inside one term (docs/implementation_plan_2026-10-07_and_terms.md)

from src.sources.query_builder import osf_title_terms


def _text(q):
    """The text part of a Europe PMC query, without the date clause."""
    return q.split(" AND FIRST_PDATE")[0]


@pytest.mark.parametrize("group, expected", [
    ({"both": "cooperati* AND survival"}, "((TITLE_ABS:cooperati* AND TITLE_ABS:survival))"),
    ({"both": "a AND b AND c"}, "((TITLE_ABS:a AND TITLE_ABS:b AND TITLE_ABS:c))"),
    ({"title": "cooperati* AND survival"}, "((TITLE:cooperati* AND TITLE:survival))"),
    ({"abstract": "kin selection AND survival"},
     '((ABSTRACT:"kin selection" AND ABSTRACT:survival))'),
    ({"both": "cooperati* AND survival, kin selection"},
     '(((TITLE_ABS:cooperati* AND TITLE_ABS:survival) OR TITLE_ABS:"kin selection"))'),
    ({"both": "anxiety and depression"}, '(TITLE_ABS:"anxiety and depression")'),  # lowercase: phrase
    ({"both": "cooperative species survival"}, '(TITLE_ABS:"cooperative species survival")'),
])
def test_and3_europepmc_queries(group, expected):
    g = {"title": "", "abstract": "", "both": "", **group}
    assert _text(build_europepmc_query(_q([g]))) == expected


def test_and3_europepmc_and_term_in_two_groups_and_two_fields():
    q = _text(build_europepmc_query(_q([
        {"title": "stress AND cortisol", "abstract": "infant*", "both": ""},
        {"title": "", "abstract": "", "both": "sleep AND apnea"}])))
    assert q == ("((((TITLE:stress AND TITLE:cortisol)) AND (ABSTRACT:infant*)) OR "
                 "((TITLE_ABS:sleep AND TITLE_ABS:apnea)))")


def test_and3_a_term_that_is_only_and_is_ignored():
    """Same as an empty box today: no clause, never "()"."""
    q = build_europepmc_query(_q([{"title": "", "abstract": "", "both": "AND"}]))
    assert "()" not in q and q.startswith("FIRST_PDATE")
    q = build_europepmc_query(_q([{"title": "", "abstract": "", "both": "AND, sleep"}]))
    assert _text(q) == "(TITLE_ABS:sleep)"


@pytest.mark.parametrize("group, expected", [
    # TD3: the wildcard part is left out when another part can narrow arXiv.
    ({"both": "cooperati* AND survival"}, "all:survival"),
    ({"title": "kin selection AND survival"}, '(ti:"kin selection" AND ti:survival)'),
    ({"abstract": "a AND b, c"}, "((abs:a AND abs:b) OR abs:c)"),
])
def test_and4_arxiv_queries(group, expected):
    g = {"title": "", "abstract": "", "both": "", **group}
    q = build_arxiv_query(_q([g]))
    assert q.startswith(f"({expected}) AND submittedDate:")


def test_and5_osf_sends_one_part_of_an_and_term():
    fd = {"days_back": 7, "text_groups": [
        {"title": "ant* AND cooperative breeding"}, {"title": "kin selection"}]}
    assert osf_title_terms(fd, 10) == ["cooperative breeding", "kin selection"]


@pytest.mark.parametrize("term", ["ant* AND cooperative breeding", "a AND bb AND c",
                                  "survival AND cooperati*"])
def test_and5_osf_part_is_a_superset(term):
    """Any title that matches every part contains the part sent, so the
    title narrowing cannot lose a match the local filter would keep."""
    from src.filtering import term_matches
    sent = osf_title_terms({"days_back": 7, "text_groups": [{"title": term}]}, 10)[0].lower()
    titles = ["Ants and cooperative breeding", "a bb c", "Survival of cooperative birds",
              "cooperative breeding only", "bb"]
    for t in titles:
        if term_matches(term, t.lower()):
            assert sent in t.lower(), (term, t)


def test_and5_psyarxiv_keywords_list_the_parts():
    q = build_psyarxiv_query(_q([{"title": "", "abstract": "", "both": "cooperati* AND survival"}]))
    assert q == "cooperati survival"


# ── TA1/TA3: Title-or-abstract terms use Europe PMC's TITLE_ABS field ─────────
# docs/implementation_plan_2026-10-07_title_abs.md. A bare term matched full
# text too; the app keeps only title/abstract matches, after reading 200.

@pytest.mark.parametrize("both, expected", [
    ("loneliness", "(TITLE_ABS:loneliness)"),
    ("kin selection", '(TITLE_ABS:"kin selection")'),
    ("adolescen*", "(TITLE_ABS:adolescen*)"),
    ("cooperati* AND survival", "((TITLE_ABS:cooperati* AND TITLE_ABS:survival))"),
    ("sleep, apnea", "((TITLE_ABS:sleep OR TITLE_ABS:apnea))"),
])
def test_ta1_both_terms_use_title_abs(both, expected):
    q = build_europepmc_query(_q([{"title": "", "abstract": "", "both": both}]))
    assert _text(q) == expected


def test_ta1_title_and_abstract_boxes_unchanged():
    q = _text(build_europepmc_query(_q([{"title": "stress", "abstract": "cortisol", "both": ""}])))
    assert q == "((TITLE:stress) AND (ABSTRACT:cortisol))"


def test_ta3_both_terms_never_bare():
    """Adversarial (P7): every search word in the text part carries a field,
    so Europe PMC is never asked for full-text-only matches."""
    import re as _re
    q = _text(build_europepmc_query(_q([
        {"title": "", "abstract": "", "both": "cooperati* AND survival, kin selection, mice"},
        {"title": "x", "abstract": "", "both": "y AND z"}])))
    stripped = _re.sub(r'(TITLE_ABS|TITLE|ABSTRACT):("[^"]*"|\S+?)(?=[\s)])', "", q)
    leftover = [w for w in _re.findall(r"[A-Za-z*\"]+", stripped) if w not in ("AND", "OR")]
    assert leftover == [], (q, leftover)



# ── TD1/TD3 (docs/implementation_plan_2026-10-07_gate_todos.md) ──────────────

@pytest.mark.parametrize("both, expected", [
    ("COVID-19: outcomes", '(TITLE_ABS:"COVID-19: outcomes")'),
    ("COVID-19", '(TITLE_ABS:"COVID-19")'),
    ("OR", '(TITLE_ABS:"OR")'),
    ("(x)", '(TITLE_ABS:"(x)")'),
    ("adolescen*", "(TITLE_ABS:adolescen*)"),
    ("kin select*", "((TITLE_ABS:kin AND TITLE_ABS:select*))"),
    ('say "hi"', '(TITLE_ABS:"say \\"hi\\"")'),
])
def test_td1_parts_are_read_as_words(both, expected):
    assert _text(build_europepmc_query(_q([{"title": "", "abstract": "", "both": both}]))) == expected


def test_td1_injection_stays_inside_one_clause():
    """Adversarial (P7): a term written as query syntax must not widen the
    search. Every token outside quotes is a field-prefixed clause or AND/OR."""
    import re as _re
    q = _text(build_europepmc_query(_q([{"title": "", "abstract": "",
                                          "both": "x) OR (TITLE_ABS:*"}])))
    outside = _re.sub(r'"(?:[^"\\]|\\.)*"', "Q", q).replace("TITLE_ABS:", "")
    tokens = _re.findall(r"[^\s()]+", outside)
    # Outside quotes: only quoted words (Q) joined by AND — no bare OR, ")", ":".
    assert tokens and all(t in ("Q", "AND") for t in tokens), (q, tokens)


def test_td3_arxiv_keeps_wildcard():
    q = build_arxiv_query(_q([{"title": "", "abstract": "", "both": "cooperati*"}]))
    assert q.startswith("(all:cooperati*) AND submittedDate:")


def test_td3_arxiv_and_term_drops_wildcard_parts_when_others_remain():
    q = build_arxiv_query(_q([{"title": "", "abstract": "", "both": "cooperati* AND survival"}]))
    assert q.startswith("(all:survival) AND submittedDate:")
    q = build_arxiv_query(_q([{"title": "", "abstract": "", "both": "cooperati* AND surviv*"}]))
    assert q.startswith("((all:cooperati* AND all:surviv*)) AND submittedDate:")
