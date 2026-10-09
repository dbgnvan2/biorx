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
              "Rodenticide poisoning in toddlers",
              # Re-gate 3: prefix without a hyphen, and the right-hand side
              # of the hyphen (F7).
              "Anti mouse antibody staining of human biopsies",
              "A mouse-human chimeric antibody in lymphoma patients",
              # Re-gate 4 F9: the other word order, both spellings.
              "A human-mouse chimeric antibody in lymphoma patients",
              "Human mouse chimeric antibody therapy",
              "Rat liver microsomal metabolism of a new antiepileptic",
              "Mouse-mouse hybridoma production of human-reactive antibodies",
              "Rat-tail collagen scaffolds for human keratinocytes",
              "Rat liver microsomes predict human drug clearance",
              "Rat-brain homogenate as a binding control",
              "Mouse-skin extract in a patch test"]
    kept = filter_papers([_t(t) for t in titles], {"species": "no-animal"})
    assert [p["title"] for p in kept] == titles


def test_br7_hyphenated_animal_titles_and_plural_models_still_drop():
    """Hyphenated animal titles on either side still drop (re-gate 3 F8); F5 plural models."""
    titles = ["Mouse-derived intestinal organoids", "Rat-specific gene expression atlas",
              "Bovine models of tuberculosis", "Ovine models of fetal growth",
              "Canine models of osteosarcoma", "Equine models of asthma",
              # F6: only the CHO cell line is an exception, not any hamster ovary.
              "Syrian hamster ovary cells after infection",
              # Re-gate 3 F8: a hyphen before the animal word is still an
              # animal study unless the prefix is a reagent prefix.
              "A knockout-mouse model of colitis", "Transgenic-mouse studies of amyloid",
              "SCID-mouse xenograft growth", "Nude-mouse xenograft of melanoma",
              "Wild-type-mouse controls for gut microbiota", "Germ-free-mouse colonization",
              "A knockout-rat model of hypertension",
              # Only the prefix phrase is taken out: the rest still counts.
              "An anti-mouse antibody for immunostaining in mice"]
    assert filter_papers([_t(t) for t in titles], {"species": "no-animal"}) == []


# ── AND2: "a AND b" inside one term (docs/implementation_plan_2026-10-07_and_terms.md)

def _group(**fields):
    return {"title": "", "abstract": "", "both": "", **fields}


def test_and2_all_parts_must_match():
    """Adversarial (P7): mentions cooperation, not survival — must not match."""
    g = _group(both="cooperati* AND survival")
    only_one = {"title": "Cooperation in ant colonies", "abstract": "Foraging and nests."}
    both_any_order = {"title": "Survival of meerkat groups",
                      "abstract": "Cooperative breeding raises pup survival."}
    split_fields = {"title": "Cooperative hunting", "abstract": "Survival rates rose."}
    assert not text_group_matches(only_one, g)
    assert text_group_matches(both_any_order, g)
    assert text_group_matches(split_fields, g)      # title-or-abstract: either field


def test_and2_comma_still_ors_and_terms():
    g = _group(both="cooperati* AND survival, kin selection")
    assert text_group_matches({"title": "Kin selection revisited", "abstract": ""}, g)
    assert not text_group_matches({"title": "Cooperation", "abstract": "kin"}, g)


def test_and2_title_field_needs_all_parts_in_title():
    g = _group(title="cooperati* AND survival")
    assert not text_group_matches({"title": "Cooperative hunting",
                                   "abstract": "Survival rates rose."}, g)
    assert text_group_matches({"title": "Cooperation and survival", "abstract": ""}, g)


def test_and2_phrase_part_is_still_a_phrase():
    g = _group(both="kin selection AND survival")
    assert not text_group_matches({"title": "Selection of kin and survival", "abstract": ""}, g)
    assert text_group_matches({"title": "Kin selection and survival", "abstract": ""}, g)


def test_and2_lowercase_and_is_a_phrase():
    g = _group(both="anxiety and depression")
    assert not text_group_matches({"title": "Depression and anxiety", "abstract": ""}, g)
    assert text_group_matches({"title": "Anxiety and depression in teens", "abstract": ""}, g)


def test_and2_through_filter_papers():
    papers = [{"title": "Cooperation in ants", "abstract": ""},
              {"title": "Cooperation and survival", "abstract": ""}]
    kept = filter_papers(papers, {"text_groups": [_group(both="cooperati* AND survival")]})
    assert [p["title"] for p in kept] == ["Cooperation and survival"]


# ── SW1: search within results (docs/implementation_plan_2026-10-07_search_within.md)

from src.filtering import within_matches


def test_sw1_every_term_must_match():
    p = {"title": "Infant cortisol and sleep", "abstract": "Maternal stress study."}
    assert within_matches(p, ["cortisol", "infant*"])
    assert within_matches(p, ["cortisol AND maternal"])
    assert not within_matches(p, ["cortisol", "adolescen*"])


def test_sw1_commas_or_inside_a_term():
    p = {"title": "Infant sleep", "abstract": ""}
    assert within_matches(p, ["cortisol, sleep"])
    assert not within_matches(p, ["cortisol, melatonin"])


def test_sw1_only_title_and_abstract_count():
    """Adversarial (P7): the word is in the journal and authors, not the text."""
    p = {"title": "Sleep in toddlers", "abstract": "Actigraphy.",
         "journal": "Cortisol Research", "authors": "Cortisol, A."}
    assert not within_matches(p, ["cortisol"])


def test_sw1_no_terms_matches_everything_and_empty_terms_are_ignored():
    p = {"title": "x", "abstract": ""}
    assert within_matches(p, [])
    assert within_matches(p, ["", "  ", "AND"])


# ── TD4/TD5 (docs/implementation_plan_2026-10-07_gate_todos.md) ──────────────

@pytest.mark.parametrize("term, title, expected", [
    ("kin selection", "Evidence for kin-selection in sharks", True),
    ("kin-selection", "Kin selection revisited", True),
    ("kin selection", "Kin‑selection (non-breaking hyphen)", True),
    ("COVID-19", "Outcomes after COVID-19", True),
    ("covid 19", "Outcomes after COVID-19", True),
    ("CD4+", "CD4+ T cells", True),
    ("COVID-19: outcomes", "COVID-19 outcomes in adults", True),   # punctuation as space
    ("covid 19 outcomes", "COVID-19: outcomes", True),
    ("long-term", "A longterm study", False),            # hyphen is a space, not nothing
    ("kin selection", "Selection of kin", False),
])
def test_td4_hyphen_matches_space(term, title, expected):
    assert text_group_matches({"title": title, "abstract": ""},
                              {"title": "", "abstract": "", "both": term}) is expected


def test_td5_phrase_does_not_span_title_and_abstract():
    """Adversarial (P7): the phrase appears only across the join."""
    p = {"title": "Morning cortisol", "abstract": "Sleep was measured."}
    g = {"title": "", "abstract": "", "both": "cortisol sleep"}
    assert not text_group_matches(p, g)
    assert not within_matches(p, ["cortisol sleep"])
    # Separate AND parts may still be in different fields.
    assert text_group_matches(p, {"title": "", "abstract": "", "both": "cortisol AND sleep"})
    assert within_matches(p, ["cortisol AND sleep"])


def test_td7_fixed_dates_keeps_legacy_and_explicit_ranges():
    from datetime import date, timedelta
    from src.filtering import fixed_dates
    assert fixed_dates({"date_from": "2020-01-01", "date_to": "2020-12-31"})[
        "start_date"] == "2020-01-01"
    fd = fixed_dates({"start_date": "2021-03-01", "end_date": "2021-04-01", "days_back": 7})
    assert (fd["start_date"], fd["end_date"]) == ("2021-03-01", "2021-04-01")
    fd = fixed_dates({"days_back": 10})
    assert fd["end_date"] == date.today().isoformat()
    assert fd["start_date"] == (date.today() - timedelta(days=10)).isoformat()


# ── AY: All years ─────────────────────────────────────────────────────────────
# docs/implementation_plan_2026-10-08_date_window.md

def test_ay1_all_years_dates():
    """All years starts at the configured date and ends today; days_back and
    stored dates are ignored."""
    from datetime import date
    from src.filtering import date_window, fixed_dates
    cfg = {"search": {"all_years_start": "1900-01-01"}}
    for extra in ({}, {"days_back": 30}, {"start_date": "2020-01-01", "end_date": "2020-12-31"}):
        fd = fixed_dates({"all_years": True, **extra}, cfg)
        assert (fd["start_date"], fd["end_date"]) == ("1900-01-01", date.today().isoformat())
        assert date_window(fd) == {"start": "1900-01-01", "end": date.today().isoformat(),
                                   "all_years": True}
    # Without a config passed, the real sources_config.yaml is read.
    assert fixed_dates({"all_years": True})["start_date"] == "1900-01-01"


@pytest.mark.parametrize("value", [False, "false", "true", 0, 1, None, "yes"])
def test_ay2_only_true_widens(value):
    """Adversarial (P7): anything but a real true keeps the filter's own window."""
    from datetime import date, timedelta
    from src.filtering import date_window, fixed_dates
    fd = fixed_dates({"all_years": value, "days_back": 30},
                     {"search": {"all_years_start": "1900-01-01"}})
    assert fd["start_date"] == (date.today() - timedelta(days=30)).isoformat()
    assert date_window(fd)["all_years"] is False
    fd = fixed_dates({"days_back": 30})                     # key missing
    assert fd["start_date"] == (date.today() - timedelta(days=30)).isoformat()


def test_ay2_non_bool_all_years_warns_not_silent(caplog):
    """A malformed all_years on the run path must not widen, and must say so —
    not narrow in silence (review P5/P10: the saved-filter route refuses it,
    the search route and monitor must not accept it quietly)."""
    from datetime import date, timedelta
    from src.filtering import date_window, fixed_dates
    with caplog.at_level("WARNING"):
        fd = fixed_dates({"all_years": "true", "days_back": 30},
                         {"search": {"all_years_start": "1900-01-01"}})
    assert fd["start_date"] == (date.today() - timedelta(days=30)).isoformat()
    assert date_window(fd)["all_years"] is False
    assert "all_years" not in fd
    assert any("all_years" in r.getMessage() for r in caplog.records)


# ── KW5/KW6: the title-or-abstract box matches the authors' keywords ─────────
# docs/implementation_plan_2026-10-08_author_keywords.md

_IFS = {"text_groups": [{"title": "", "abstract": "", "both": "internal family systems"}]}


def test_kw5_keyword_match_kept():
    p = {"title": "Approaches to ketamine-assisted couple therapy", "abstract": "Couples.",
         "keywords": ["Ketamine", "Internal Family Systems Therapy"]}
    assert filter_papers([p], _IFS) == [p]
    # AND parts may be in different fields, as for title and abstract.
    assert text_group_matches(p, {"both": "ketamine AND internal family systems"})
    assert text_group_matches({**p, "keywords": "Internal Family Systems"}, {"both": "internal famil*"})


def test_kw5_within_uses_keywords():
    p = {"title": "Sleep in toddlers", "abstract": "Actigraphy.", "keywords": ["Cortisol"]}
    assert within_matches(p, ["cortisol"])


def test_kw6_phrase_does_not_span_two_keywords():
    """Adversarial (P7): 'family systems' is not in one keyword."""
    p = {"title": "x", "abstract": "y", "keywords": ["internal family", "systems theory"]}
    assert not text_group_matches(p, {"both": "family systems"})
    assert not within_matches(p, ["family systems"])


def test_kw6_title_box_ignores_keywords_and_no_keywords_is_unchanged():
    p = {"title": "x", "abstract": "y", "keywords": ["Internal Family Systems"]}
    assert not text_group_matches(p, {"title": "internal family systems"})
    assert not text_group_matches(p, {"abstract": "internal family systems"})
    for kw in (None, [], "", [None, 3]):
        assert not text_group_matches({"title": "x", "abstract": "y", "keywords": kw},
                                      {"both": "internal family systems"})
        assert text_group_matches({"title": "Internal family systems", "abstract": "",
                                   "keywords": kw}, {"both": "internal family systems"})


def test_gn4_filter_uses_the_shared_reader():
    """GN4: the filter reads keywords with schema.keyword_list."""
    from src.filtering import keyword_fields
    from src.sources.schema import keyword_list
    for value in (["  Parts  ", "", None, 3], "Internal Family Systems", None, 5, {"a": 1}):
        assert keyword_fields({"keywords": value}) == keyword_list(value)
    p = {"title": "x", "abstract": "y", "keywords": "Internal Family Systems"}
    assert text_group_matches(p, {"both": "internal family systems"})   # not split into letters
