"""
Client-side filter semantics for a filter_dict.

Purpose: one implementation of "what does this saved filter match?", shared by
         every front end (PyQt6 GUI, headless CLI) so they cannot drift apart.
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
from typing import Any, Dict, List

from src import filter_vocabulary as vocab

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
    out = dict(f or {})
    out["license"] = vocab.ANY
    return out


def split_terms(s: str) -> List[str]:
    """Split a comma-separated field into non-empty lowercase terms."""
    return [t.strip().lower() for t in s.split(",") if t.strip()]


def normalize_authors(value: Any) -> List[str]:
    """
    Read a filter_dict's `authors` field into a flat list of author terms.

    The GUI stores this as a list of strings; hand-written filters and the
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
        prefix = term[:-1]
        return re.search(r"(?<!\w)" + re.escape(prefix), text) is not None
    return term in text


def text_group_matches(paper: Dict[str, Any], group: Dict[str, str]) -> bool:
    """
    A group is a set of AND conditions.
    Each field may have comma-separated terms — any term in that field matches (OR within field).
    Terms ending in '*' use prefix (begins-with) matching.
    All non-empty fields must match (AND between fields).
    """
    title    = (paper.get("title")    or "").lower()
    abstract = (paper.get("abstract") or "").lower()

    title_terms    = split_terms(group.get("title",    ""))
    abstract_terms = split_terms(group.get("abstract", ""))
    both_terms     = split_terms(group.get("both",     ""))

    if title_terms    and not any(match_term(t, title)              for t in title_terms):
        return False
    if abstract_terms and not any(match_term(t, abstract)           for t in abstract_terms):
        return False
    if both_terms     and not any(match_term(t, f"{title} {abstract}") for t in both_terms):
        return False
    return True


def filter_papers(papers: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply filter criteria to a list of papers. No API calls.

    Facets compare vocabulary ids (filter_vocabulary.yaml) against the
    record's own values: paper type against document_type, licence after
    reduction to an id (review S3/B5).
    """
    f = normalise_filter(f)
    text_groups = f.get("text_groups", [])
    if not text_groups and f.get("keywords"):
        text_groups = [{"title": "", "abstract": "", "both": ", ".join(f["keywords"])}]

    authors     = normalize_authors(f.get("authors"))
    paper_type  = f.get("paper_type", vocab.ANY)
    version     = f.get("version", vocab.ANY)
    published   = f.get("published", vocab.ANY)
    license_    = f.get("license", vocab.ANY)

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
        out.append(p)
    return out
