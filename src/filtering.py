"""
Client-side filter semantics for a filter_dict.

Purpose: one implementation of "what does this saved filter match?", shared by
         every front end (web app, headless CLI) so they cannot drift apart.
Spec:    ARXIV_ADAPTER_TASK.md (review finding 4 — CLI must reproduce GUI filtering)
Tests:   tests/test_filtering.py

Source queries are necessarily approximate — each API has its own field syntax,
and several filter facets (paper_type, version, published, license, institution)
have no equivalent at all on preprint servers. Every front end therefore
re-applies the full filter to the records a search returns. This module is that
re-application; it makes no API calls.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from src import filter_vocabulary as vocab
from src.search_terms import and_parts, normalise_text

logger = logging.getLogger(__name__)


def normalise_filter(f: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a filter_dict in the canonical (desktop) shape.

    An earlier web build saved three fields in shapes nothing else reads:
      * text groups as {"keywords": ...}  -> "both" (else the group matched all)
      * date_from / date_to               -> start_date / end_date (else ignored)
      * institution as a list             -> a string (else .strip() crashed)
      * keywords as a string              -> a list (else split into letters)
    Applied wherever a stored or submitted filter is read or run, so the fix
    does not depend on the filter being re-saved from the editor (P19).
    """
    out = dict(f or {})
    groups = []
    for g in out.get("text_groups") or []:
        g = dict(g or {})
        if "keywords" in g:
            legacy = g.pop("keywords") or ""
            if legacy and not g.get("both"):
                g["both"] = legacy
        groups.append(g)
    if "text_groups" in out:
        out["text_groups"] = groups
    for old, new in (("date_from", "start_date"), ("date_to", "end_date")):
        if old in out:
            value = out.pop(old)
            if value and not out.get(new):
                out[new] = value
    # A top-level keywords string was joined character by character by every
    # reader (", ".join("stress") -> "s, t, r, e, s, s"). Read it as the
    # comma-separated list it was meant to be.
    kw = out.get("keywords")
    if isinstance(kw, str):
        out["keywords"] = [k.strip() for k in kw.split(",") if k.strip()]
    inst = out.get("institution")
    if isinstance(inst, (list, tuple)):
        out["institution"] = ", ".join(str(i).strip() for i in inst if str(i).strip())
    elif inst is None and "institution" in out:
        out["institution"] = ""
    # No source reports author institutions (review B5): an institution term
    # matched nothing, so a filter with one returned no papers. Saving one is
    # refused (filter_vocabulary.problems); a stored one is ignored, loudly.
    if (out.get("institution") or "").strip():
        logger.warning("Filter %r: ignoring institution %r — no source reports it",
                       out.get("name", ""), out["institution"])
        out["institution"] = ""
    # Facet values are vocabulary ids (review S3). Legacy display labels map
    # to ids; a value with no equivalent any more, or an unknown one, is
    # dropped with a warning rather than guessed at.
    for facet in vocab.FACETS:
        if facet not in out:
            continue
        try:
            value = vocab.normalise_value(facet, out[facet])
        except vocab.UnknownValue as exc:
            logger.warning("Filter %r: ignoring %s", out.get("name", ""), exc)
            value = vocab.ANY
        if value is None:
            logger.warning("Filter %r: %s %r is no longer supported; ignoring it",
                           out.get("name", ""), facet, out[facet])
            value = vocab.ANY
        out[facet] = value
    return out


def without_license(f: Dict[str, Any]) -> Dict[str, Any]:
    """The filter minus its licence condition, for use before enrichment.

    Purpose: Keep papers whose licence only enrichment can supply.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B5
    Tests:   tests/test_monitor.py::test_b5_license_filter_sees_enriched_license,
             tests/web/test_searches_routes.py::test_b5_license_filter_sees_enriched_license

    Unpaywall and Crossref fill in a licence the search source did not
    report. Callers choose what to enrich with this, and apply the full
    filter once enrichment has run.
    """
    out = normalise_filter(f)
    out["license"] = vocab.ANY
    return out


def split_terms(s: str) -> List[str]:
    """Split a comma-separated field into non-empty terms (alternatives).

    Case is kept: an uppercase AND inside a term is an operator
    (src/search_terms.py), so lowercasing waits until term_matches.
    """
    return [t.strip() for t in (s or "").split(",") if and_parts(t)]


def term_matches(term: str, *fields: str) -> bool:
    """A term against one or more fields: every AND part must match, each
    part within one field (different parts may be in different fields).

    Text and term are normalised first (lowercase, hyphens as spaces, TD4).
    A phrase does not match across two fields (TD5: title "…cortisol" +
    abstract "Sleep…" is not "cortisol sleep").

    Purpose: "cooperati* AND survival" requires both words; a term without
             AND matches as before.
    Spec:    docs/implementation_plan_2026-10-07_and_terms.md#AND2,
             docs/implementation_plan_2026-10-07_gate_todos.md#TD4, #TD5
    Tests:   tests/test_filtering.py::test_and2_all_parts_must_match,
             tests/test_filtering.py::test_td4_hyphen_matches_space,
             tests/test_filtering.py::test_td5_phrase_does_not_span_title_and_abstract
    """
    parts = [normalise_text(p) for p in and_parts(term)]
    texts = [normalise_text(f) for f in fields]
    return bool(parts) and all(any(match_term(p, t) for t in texts) for p in parts)


def normalize_authors(value: Any) -> List[str]:
    """
    Read a filter_dict's `authors` field into a flat list of author terms.

    The saved form is a list of strings; hand-written filters and the
    arXiv query builder's original contract used a single comma-separated
    string. Both shapes are accepted here so that every consumer agrees on
    what the field means — iterating a string character-by-character, or
    calling .split() on a list, are the two failure modes this prevents.
    """
    if not value:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [v for v in value if isinstance(v, str)]
    else:
        return []

    out: List[str] = []
    for item in items:
        for part in item.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def wildcard_pattern(term: str) -> "re.Pattern[str]":
    """The pattern for a term ending in '*': the rest is the start of a word.

    Shared by match_term and the Discover hit counts (src/discover.py), so
    the two cannot drift (QA gate 2026-10-07 finding 2).
    """
    return re.compile(r"(?<!\w)" + re.escape(term[:-1]))


def match_term(term: str, text: str) -> bool:
    """Match a single term against text. term ending in '*' = prefix of any word.

    Purpose: One meaning for a wildcard term on every front end.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B4
    Tests:   tests/test_filtering.py::test_b4_wildcard_matches_mid_title,
             tests/test_filtering.py::test_b4_wildcard_does_not_match_inside_word

    "adolescen*" matches "Stress in adolescents" but not "preadolescent", as
    Europe PMC's Lucene wildcard does. A plain term is a substring match.
    """
    if term.endswith("*"):
        return wildcard_pattern(term).search(text) is not None
    return term in text


def text_group_matches(paper: Dict[str, Any], group: Dict[str, str]) -> bool:
    """
    A group is a set of AND conditions.
    Each field may have comma-separated terms — any term in that field matches (OR within field).
    A term with uppercase AND needs every part (AND within a term).
    Terms ending in '*' use prefix (begins-with) matching.
    All non-empty fields must match (AND between fields).
    """
    title    = (paper.get("title")    or "").lower()
    abstract = (paper.get("abstract") or "").lower()

    title_terms    = split_terms(group.get("title",    ""))
    abstract_terms = split_terms(group.get("abstract", ""))
    both_terms     = split_terms(group.get("both",     ""))

    if title_terms    and not any(term_matches(t, title)           for t in title_terms):
        return False
    if abstract_terms and not any(term_matches(t, abstract)        for t in abstract_terms):
        return False
    if both_terms     and not any(term_matches(t, title, abstract) for t in both_terms):
        return False
    return True


def fixed_dates(filter_dict: Dict[str, Any],
                sources_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The filter with its date window written out once (start_date/end_date),
    so every source and the bioRxiv/medRxiv note use the same dates even if
    the search runs past midnight (TD7).

    Purpose: One date window per search; All years starts it at the
             configured earliest date (AY1).
    Spec:    docs/implementation_plan_2026-10-07_gate_todos.md#TD7,
             docs/implementation_plan_2026-10-08_date_window.md#AY1, #AY2
    Tests:   tests/web/test_searches_routes.py::test_td7_dates_fixed_once,
             tests/test_filtering.py::test_ay1_all_years_dates,
             tests/test_filtering.py::test_ay2_only_true_widens
    """
    from src.sources.query_builder import get_date_range
    # Legacy keys (date_from/date_to) first, or their dates would be replaced
    # by days_back's window (caught by test_b13_legacy_filter_normalised).
    filter_dict = normalise_filter(filter_dict)
    # AY2: 'all years' is a real bool everywhere. A present but non-bool value
    # is dropped with a warning, so a malformed value cannot silently narrow
    # the window on the run paths (search route and monitor) that do not
    # refuse it outright the way the saved-filter routes do (_check_all_years).
    if (filter_dict.get("all_years") is not None
            and not isinstance(filter_dict.get("all_years"), bool)):
        logger.warning("all_years is not true or false (%r) — ignoring it; "
                       "only a real true widens the window",
                       filter_dict.get("all_years"))
        filter_dict.pop("all_years")
    if filter_dict.get("all_years") is True:
        from src.search_limits import all_years_start
        if sources_config is None:
            from src.sources.config import load_sources_config
            sources_config = load_sources_config()
        start = all_years_start(sources_config)
        end = datetime.today().strftime("%Y-%m-%d")
    else:
        start, end = get_date_range(filter_dict)
    return {**filter_dict, "start_date": start, "end_date": end}


def date_window(filter_dict: Dict[str, Any]) -> Dict[str, Any]:
    """The window a fixed_dates filter searches, for the job and the page.

    Purpose: Say which dates a search covered (DW1).
    Spec:    docs/implementation_plan_2026-10-08_date_window.md#DW1
    Tests:   tests/web/test_searches_routes.py::test_dw1_window_on_the_job
    """
    return {"start": filter_dict.get("start_date") or "",
            "end": filter_dict.get("end_date") or "",
            "all_years": filter_dict.get("all_years") is True}


def within_matches(paper: Dict[str, Any], terms: List[str]) -> bool:
    """A paper against search-within terms: every term must match its title
    or abstract (commas = alternatives, AND = all parts, as in any box).
    Empty terms, and terms that are only "AND", are ignored.

    Purpose: Narrow a finished search's results without asking any source.
    Spec:    docs/implementation_plan_2026-10-07_search_within.md#SW1
    Tests:   tests/test_filtering.py::test_sw1_every_term_must_match,
             tests/test_filtering.py::test_sw1_only_title_and_abstract_count
    """
    title, abstract = paper.get("title") or "", paper.get("abstract") or ""
    for term in terms:
        alternatives = split_terms(term)
        if alternatives and not any(term_matches(t, title, abstract) for t in alternatives):
            return False
    return True


def filter_papers(papers: List[Dict[str, Any]], f: Dict[str, Any], *,
                  normalised: bool = False) -> List[Dict[str, Any]]:
    """Apply filter criteria to a list of papers. No API calls.

    Facets compare vocabulary ids (filter_vocabulary.yaml) against the
    record's own values: paper type against document_type, licence after
    reduction to an id (review S3/B5), and "human"/"no-animal" species
    against the title's animal terms.

    normalised=True: the caller already ran normalise_filter, so it is not
    repeated — the enrichment gate calls this once per record (batch-1 gate
    note 1, folded into review M11).
    """
    if not normalised:
        f = normalise_filter(f)
    text_groups = f.get("text_groups", [])
    if not text_groups and f.get("keywords"):
        text_groups = [{"title": "", "abstract": "", "both": ", ".join(f["keywords"])}]

    authors     = normalize_authors(f.get("authors"))
    paper_type  = f.get("paper_type", vocab.ANY)
    version     = f.get("version", vocab.ANY)
    published   = f.get("published", vocab.ANY)
    license_    = f.get("license", vocab.ANY)
    species     = f.get("species", vocab.ANY)

    out = []
    for p in papers:
        auth_str = f"{p.get('authors','').lower()} {p.get('author_corresponding','').lower()}"
        ver      = str(p.get("version") or "")
        pub      = (p.get("published") or "NA")

        if text_groups and not any(text_group_matches(p, g) for g in text_groups):
            continue
        if authors and not any(match_term(a.lower(), auth_str) for a in authors):
            continue
        if paper_type != vocab.ANY and not vocab.paper_type_matches(paper_type, p.get("type") or ""):
            continue
        if version == "first" and ver != "1":
            continue
        if version == "revised" and (not ver.isdigit() or int(ver) < 2):
            continue
        if published == "preprint" and pub != "NA":
            continue
        if published == "journal" and pub == "NA":
            continue
        if license_ != vocab.ANY and vocab.license_id(p.get("license") or "") != license_:
            continue
        # Only the Europe PMC/PubMed query applied species before; a title
        # naming an animal study is now dropped for every source (browser run
        # 2026-09-29). "animal" stays query-only (see filter_vocabulary.yaml).
        if species in ("human", "no-animal") and vocab.title_names_an_animal_study(p.get("title") or ""):
            continue
        out.append(p)
    return out
