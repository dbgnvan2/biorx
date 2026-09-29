"""
Tests for src/filtering.py — the single implementation of a saved filter's
client-side semantics, shared by the GUI and the headless CLI.

Covers review findings 1 (authors type contract) and 4 (CLI must filter).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.filtering import filter_papers, normalize_authors, text_group_matches

REPO_ROOT = Path(__file__).parent.parent


def _paper(**kw):
    base = {
        "title": "Generative Agents", "abstract": "A study of personas.",
        "authors": "Park J; Smith A", "author_corresponding": "Park J",
        "author_corresponding_institution": "Stanford",
        "type": "preprint", "version": "1", "published": "NA", "license": "cc_by",
    }
    base.update(kw)
    return base


# ── normalize_authors: the shape contract both readers must agree on ──────────

def test_normalize_authors_accepts_the_gui_list_shape():
    assert normalize_authors(["Smith", "Jones"]) == ["Smith", "Jones"]


def test_normalize_authors_accepts_a_comma_separated_string():
    """A string must not be iterated character-by-character."""
    assert normalize_authors("Smith, Jones") == ["Smith", "Jones"]


def test_normalize_authors_splits_commas_inside_list_entries():
    assert normalize_authors(["Smith, Jones", "Lee"]) == ["Smith", "Jones", "Lee"]


@pytest.mark.parametrize("empty", [None, "", [], (), 0, {"a": 1}])
def test_normalize_authors_empty_and_unsupported_shapes_yield_no_terms(empty):
    assert normalize_authors(empty) == []


# ── filter_papers ─────────────────────────────────────────────────────────────

def test_filter_papers_author_filter_as_string_does_not_match_every_paper():
    """
    Regression: `[a for a in f["authors"]]` over a string yields single
    characters, and 'p' is in almost every author list — so a string-shaped
    author filter silently matched everything.
    """
    papers = [_paper(authors="Zylstra Q", author_corresponding="Zylstra Q")]
    assert filter_papers(papers, {"authors": "Park"}) == []
    assert len(filter_papers(papers, {"authors": "Zylstra"})) == 1


def test_filter_papers_author_filter_as_list_matches():
    papers = [_paper()]
    assert len(filter_papers(papers, {"authors": ["Park"]})) == 1
    assert filter_papers(papers, {"authors": ["Nobody"]}) == []


def test_filter_papers_applies_text_groups():
    papers = [_paper(title="Generative Agents"), _paper(title="Protein Folding", abstract="x")]
    groups = [{"title": "", "abstract": "", "both": "generative agents"}]
    out = filter_papers(papers, {"text_groups": groups, "authors": []})
    assert [p["title"] for p in out] == ["Generative Agents"]


def test_filter_papers_applies_non_text_facets():
    """paper_type/version/published/license have no query-time equivalent."""
    papers = [_paper(version="1"), _paper(version="3", title="Revised")]
    out = filter_papers(papers, {"version": "2+ (revised only)"})
    assert [p["title"] for p in out] == ["Revised"]


def test_text_group_matches_supports_prefix_wildcard():
    assert text_group_matches(_paper(title="Adolescent stress"),
                              {"title": "adolescen*", "abstract": "", "both": ""})
    assert not text_group_matches(_paper(title="Adult stress"),
                                  {"title": "adolescen*", "abstract": "", "both": ""})


# ── Real artifact, not a hand-built dict ──────────────────────────────────────

def test_every_saved_filter_in_filters_json_is_readable_by_the_filter():
    """
    The shapes in the live filters.json are the contract. Hand-built fixtures
    are what let the `authors`-as-string assumption survive review.
    """
    # The seed file always; the owner's filters.json too when this machine
    # has one (it is local state, not in git — review M32).
    saved = []
    for name in ("filters.seed.json", "filters.json"):
        if (REPO_ROOT / name).exists():
            data = json.loads((REPO_ROOT / name).read_text())
            saved += data["filters"] if isinstance(data, dict) and "filters" in data else data
    assert saved, "no saved filters to check against"

    papers = [_paper()]
    for f in saved:
        filter_papers(papers, f)          # must not raise on any real filter
        normalize_authors(f.get("authors"))


def test_gui_uses_the_shared_implementation():
    """
    The GUI must not keep a private copy — two implementations of one predicate
    drift. Skipped where PyQt6 is unavailable.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    assert gui._filter_papers is filter_papers
    assert gui._text_group_matches is text_group_matches


# ── B4: a wildcard is a word prefix, not a text prefix ───────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B4
# match_term used text.startswith(prefix), so "adolescen*" matched only titles
# that begin with it and dropped "Stress in adolescents" on every front end.

def test_b4_wildcard_matches_mid_title():
    from src.filtering import filter_papers
    paper = {"title": "Stress in adolescents", "abstract": ""}
    f = {"text_groups": [{"title": "adolescen*", "abstract": "", "both": ""}]}
    assert filter_papers([paper], f) == [paper]


def test_b4_wildcard_does_not_match_inside_word():
    """Adversarial: the prefix inside a longer word is not a word prefix."""
    from src.filtering import match_term
    assert not match_term("adolescen*", "preadolescent children")
    assert match_term("adolescen*", "pre-adolescent children")   # hyphen is a word break
    assert match_term("adolescen*", "adolescence")


def test_b4_wildcard_escapes_regex_characters():
    from src.filtering import match_term
    assert match_term("c++*", "a c++ library")
    assert not match_term("a.b*", "axb")


def test_m11_normalised_filter_is_not_normalised_again():
    """Batch-1 gate note 1 (folded into M11): the enrichment gate calls
    filter_papers once per record with a filter normalised up front."""
    from unittest.mock import patch
    from src import filtering
    f = filtering.without_license({"text_groups": [{"both": "x"}], "license": "cc_by"})
    assert f["license"] == "any"
    with patch.object(filtering, "normalise_filter", side_effect=AssertionError("again")):
        assert filtering.filter_papers([{"title": "x", "abstract": ""}], f, normalised=True)


# ── Species re-checked here (browser run 2026-09-29) ──────────────────────────

def _t(title):
    return {"title": title, "abstract": "", "authors": ""}


def test_br7_no_animal_drops_animal_titles():
    """The run found "… Carrageenan-Induced Inflammation in Rats" passing
    "exclude animal studies": the flag was only a Europe PMC query clause."""
    papers = [_t("Photobiomodulation Modulates Inflammation-Related Genes Following "
                 "Carrageenan-Induced Inflammation in Rats"),
              _t("Stress responses in mice"),
              _t("Nrf2 in a murine model of brain injury"),
              _t("Microglia in zebrafish larvae"),
              _t("Vaccine responses in non-human primates"),
              _t("Inflammation and delirium in critically ill patients")]
    for species in ("no-animal", "human", "Exclude animal studies"):
        kept = filter_papers(papers, {"species": species})
        assert [p["title"] for p in kept] == ["Inflammation and delirium in critically ill patients"]


def test_br7_human_studies_that_look_close_are_kept():
    """Adversarial: words that contain an animal term, and pets."""
    titles = ["Ratio of inflammatory markers in migrants", "Dog ownership and loneliness",
              "Separating stress from strain", "Pirates, parrots and prosociality",
              "Mousetrap-shaped regions in human cortex",
              # QA gate 2026-09-29: organism words used as reagents, devices,
              # cell lines, anatomy, diseases, therapies and acronyms.
              "Bovine serum albumin as a carrier in human plasma assays",
              "Outcomes of porcine bioprosthetic valves in older adults",
              "A rabbit monoclonal antibody for PD-L1 staining in lung cancer",
              "Chinese hamster ovary cells for biosimilar production",
              "Impacted canine tooth in adolescents",
              "Handling missing data with MICE in a cohort study",
              "Murine typhus in returning travellers",
              "Equine-assisted therapy for veterans with PTSD"]
    kept = filter_papers([_t(t) for t in titles], {"species": "no-animal"})
    assert [p["title"] for p in kept] == titles


def test_br7_animal_and_any_are_not_filtered_here():
    papers = [_t("Stress in mice"), _t("Stress in adults")]
    assert filter_papers(papers, {"species": "animal"}) == papers
    assert filter_papers(papers, {"species": "any"}) == papers


def test_br7_terms_come_from_the_vocabulary_file():
    from src import filter_vocabulary as vocab
    terms = vocab.load()["species"]["animal_title_terms"]
    assert "rats" in terms and "dog" not in terms and "dogs" not in terms



def test_br7_singular_lab_animals_are_dropped():
    """Re-gate 2026-09-29 F3: dropping the singulars missed the commonest
    animal-study titles."""
    titles = ["Mouse embryonic stem cell differentiation", "Mouse brain development and plasticity",
              "Rat liver regeneration", "Hamster model of diet-induced obesity",
              "Macaque visual cortex recordings", "A rodent study of sleep loss",
              "Porcine model of septic shock", "A rabbit model of osteoarthritis"]
    assert filter_papers([_t(t) for t in titles], {"species": "no-animal"}) == []


def test_br7_exception_phrases_and_known_gaps():
    kept = ["Mouse-tracking reveals decision conflict in adults",
            "Computer mouse use and wrist pain in office workers",
            "Chinese hamster ovary cells for biosimilar production",
            # Known and accepted gaps: these names have human-research senses,
            # so they are matched only in "<animal> model" phrases.
            "Bovine mastitis", "Canine osteoarthritis progression", "Equine laminitis",
            "Murine leukemia virus pathogenesis"]
    got = filter_papers([_t(t) for t in kept], {"species": "no-animal"})
    assert [p["title"] for p in got] == kept


def test_br7_hyphen_compounds_with_other_senses_are_kept():
    """Re-gate 2 2026-09-29 F4: a hyphen was a word boundary, so reagents,
    human diseases and a plant were dropped as animal studies."""
    titles = ["Validation of an anti-mouse antibody panel for human flow cytometry",
              "Anti-rat IgG cross-reactivity in patient sera",
              "An anti-rodent antibody control in human tissue staining",
              "Rat-bite fever in a child", "Rat bite fever: a case series",
              "Rat lungworm angiostrongyliasis in Hawaii",
              "Mouse-ear cress root development",
              "Burnout and the rat race among junior doctors",
              "Rodenticide poisoning in toddlers"]
    kept = filter_papers([_t(t) for t in titles], {"species": "no-animal"})
    assert [p["title"] for p in kept] == titles


def test_br7_hyphen_on_the_right_and_plural_models_still_drop():
    """The F4 boundary change must not lose animal titles; F5 plural models."""
    titles = ["Mouse-derived intestinal organoids", "Rat-specific gene expression atlas",
              "Bovine models of tuberculosis", "Ovine models of fetal growth",
              "Canine models of osteosarcoma", "Equine models of asthma",
              # F6: only the CHO cell line is an exception, not any hamster ovary.
              "Syrian hamster ovary cells after infection"]
    assert filter_papers([_t(t) for t in titles], {"species": "no-animal"}) == []
