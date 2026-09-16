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

from typing import Any, Dict, List


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
    """Match a single term against text. term ending in '*' = begins-with / prefix match."""
    if term.endswith("*"):
        return text.startswith(term[:-1])
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
    """Apply filter criteria to a list of papers. No API calls."""
    text_groups = f.get("text_groups", [])
    if not text_groups and f.get("keywords"):
        text_groups = [{"title": "", "abstract": "", "both": ", ".join(f["keywords"])}]

    authors     = normalize_authors(f.get("authors"))
    institution = f.get("institution", "").strip().lower()
    paper_type  = f.get("paper_type", "(any)")
    version     = f.get("version", "(any)")
    published   = f.get("published", "(any)")
    license_    = f.get("license", "(any)")

    out = []
    for p in papers:
        auth_str = f"{p.get('authors','').lower()} {p.get('author_corresponding','').lower()}"
        inst_str = (p.get("author_corresponding_institution") or "").lower()
        ptype    = (p.get("type") or "").lower()
        ver      = str(p.get("version") or "")
        pub      = (p.get("published") or "NA")
        lic      = (p.get("license") or "").lower()

        if text_groups and not any(text_group_matches(p, g) for g in text_groups):
            continue
        if authors and not any(match_term(a.lower(), auth_str) for a in authors):
            continue
        if institution and not match_term(institution, inst_str):
            continue
        if paper_type != "(any)" and paper_type.lower() not in ptype:
            continue
        if version == "1 (first submission only)" and ver != "1":
            continue
        if version == "2+ (revised only)" and (not ver.isdigit() or int(ver) < 2):
            continue
        if published == "preprints only (not in journal)" and pub != "NA":
            continue
        if published == "published in journal only" and pub == "NA":
            continue
        if license_ != "(any)" and license_.lower() not in lic:
            continue
        out.append(p)
    return out
