"""
Convert a filter_dict (a saved or ad hoc filter) into source-specific query strings.

filter_dict text_groups structure:
    [{"title": "stress,cortisol", "abstract": "", "both": "adolescen*"}, ...]

Groups are OR-joined. Within a group:
  - title terms  → must appear in title (AND between non-empty fields, OR within a field)
  - abstract terms → must appear in abstract
  - both terms   → must appear in title OR abstract

AND inside a term ("cooperati* AND survival", uppercase): every part must
  appear in that field (docs/implementation_plan_2026-10-07_and_terms.md AND3–AND5).

Wildcard: term ending in '*' = prefix match (works natively in Europe PMC Lucene and
  in Europe PMC's bare term matches — but NOT in arXiv, which has no wildcard operator).
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List

from src.search_terms import and_parts

logger = logging.getLogger(__name__)


# ── Internal helpers ────────────────────────────────────────────────────────────

def _split_terms(s: str) -> List[str]:
    """Split comma-separated field into non-empty stripped terms. A term with
    nothing but AND in it has no parts and is dropped."""
    return [t.strip() for t in (s or "").split(",") if and_parts(t)]


def _has_text(group: Dict[str, str]) -> bool:
    """True when any field of the group has a usable term."""
    return any(_split_terms(group.get(k) or "") for k in ("title", "abstract", "both"))


# Characters Lucene (Europe PMC) and arXiv read as query syntax, and the
# operator words. A part containing any of them is quoted (TD1).
_QUERY_SYNTAX = set(':()[]{}^~?\\/+-!&|"*')
_OPERATORS = {"AND", "OR", "NOT", "ANDNOT"}


def _needs_quotes(word: str) -> bool:
    """True when a part must be quoted to be read as words, not syntax."""
    return (" " in word or word in _OPERATORS
            or any(c in _QUERY_SYNTAX for c in word))


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _term_clause(term: str, field: str = "") -> str:
    """One search part as field:value, read as words.

    Purpose: A part such as "COVID-19: outcomes" or "OR" is searched as words,
             not parsed as query syntax (TD1). A trailing * stays a wildcard.
    Spec:    docs/implementation_plan_2026-10-07_gate_todos.md#TD1
    Tests:   tests/test_query_builder.py::test_td1_parts_are_read_as_words,
             tests/test_query_builder.py::test_td1_injection_stays_inside_one_clause

    A phrase with a trailing * (e.g. "kin select*") cannot be sent as a
    quoted wildcard (Europe PMC finds 0 either way), so its words are sent as
    separate parts with the wildcard on the last: a superset of the phrase,
    which the local filter then checks.
    """
    pre = f"{field}:" if field else ""
    if term.endswith("*"):
        stem = term[:-1]
        if not _needs_quotes(stem):
            return f"{pre}{stem}*"
        # Split where the local filter splits (spaces and punctuation, TD4),
        # so "COVID-1*" is sent as COVID AND 1*, not the exact word "COVID-1"
        # (HW1). With the trailing punctuation gone ("SARS-CoV-*") there is
        # no word left to prefix, and the words alone are sent.
        words = [w for w in re.split(r"[^\w]+", stem) if w]
        if not words:
            return f"{pre}{_quoted(stem)}"
        trailing_cut = not stem[-1:].isalnum() and not stem.endswith("_")
        parts = [_term_clause(w, field) for w in words[:-1]]
        parts.append(_term_clause(words[-1] if trailing_cut else words[-1] + "*", field))
        return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"
    return f"{pre}{_quoted(term)}" if _needs_quotes(term) else f"{pre}{term}"


def _lucene_term(term: str, field: str = "") -> str:
    """Wrap a single term with an optional Lucene field prefix."""
    return _term_clause(term, field)


def _lucene_clause(term: str, field: str = "") -> str:
    """One comma-separated term: a single term, or its AND parts in brackets.

    Purpose: Send "a AND b" to Europe PMC as both words, not one phrase.
    Spec:    docs/implementation_plan_2026-10-07_and_terms.md#AND3
    Tests:   tests/test_query_builder.py::test_and3_europepmc_queries
    """
    parts = and_parts(term)
    if len(parts) == 1:
        return _lucene_term(parts[0], field)
    return "(" + " AND ".join(_lucene_term(p, field) for p in parts) + ")"


def _group_to_lucene(group: Dict[str, str]) -> str:
    """Convert one AND-group dict to a Lucene clause string."""
    title_terms    = _split_terms(group.get("title",    ""))
    abstract_terms = _split_terms(group.get("abstract", ""))
    both_terms     = _split_terms(group.get("both",     ""))

    parts: List[str] = []

    if title_terms:
        clauses = [_lucene_clause(t, "TITLE") for t in title_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if abstract_terms:
        clauses = [_lucene_clause(t, "ABSTRACT") for t in abstract_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if both_terms:
        # "both" = title or abstract. A bare term matched every field,
        # full text included, while the app keeps only title/abstract
        # matches: most of the records read were thrown away and real
        # matches past Max results were never read (TA1).
        clauses = [_lucene_clause(t, "TITLE_ABS") for t in both_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if not parts:
        return ""

    return " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else parts[0]


def _animal_organism_exclusions() -> str:
    """NOT clauses for Europe PMC's animal flag and the organisms listed in
    filter_vocabulary.yaml (species.excluded_organisms)."""
    from src import filter_vocabulary as vocab
    parts = ["NOT ANIMAL:y"]
    for org in vocab.excluded_organisms():
        parts.append(f'NOT ORGANISM:"{org}"' if " " in org else f"NOT ORGANISM:{org}")
    return " ".join(parts)


def _species_clause(species: str) -> str:
    """Return a Europe PMC Lucene clause for the study-type / species filter.

    ANIMAL:y is a curated Europe PMC flag but is incomplete — many animal studies
    lack it.  We supplement with explicit ORGANISM exclusions for the most common
    model organisms so that untagged rodent/fish/fly papers are also excluded.
    Accepts a vocabulary id or a legacy label (review S3).
    """
    from src import filter_vocabulary as vocab
    try:
        species_id = vocab.normalise_value("species", species)
    except vocab.UnknownValue:
        logger.warning("Unknown species value %r; no species restriction applied", species)
        return ""
    if species_id in ("human", "no-animal"):
        return _animal_organism_exclusions()
    if species_id == "animal":
        return "ANIMAL:y"
    return ""  # any → no constraint


def _date_clause(days_back: int, start_date: str = "", end_date: str = "") -> str:
    """Build a Europe PMC FIRST_PDATE Lucene date clause."""
    if not end_date:
        end_date   = datetime.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    return f"FIRST_PDATE:[{start_date} TO {end_date}]"


# ── Public API ────────────────────────────────────────────────────────────────

def build_europepmc_query(filter_dict: Dict[str, Any]) -> str:
    """
    Build a Europe PMC Lucene query from a filter_dict.

    Returns a query string ready to pass to the Europe PMC REST API's `query=` param.
    """
    days_back  = filter_dict.get("days_back", 7)
    start_date = filter_dict.get("start_date", "")
    end_date   = filter_dict.get("end_date",   "")
    date       = _date_clause(days_back, start_date, end_date)

    groups = filter_dict.get("text_groups", [])
    # Back-compat: migrate old flat keywords list
    if not groups and filter_dict.get("keywords"):
        groups = [{"title": "", "abstract": "", "both": ", ".join(filter_dict["keywords"])}]

    non_empty = [
        g for g in groups
        if _has_text(g)
    ]

    species = filter_dict.get("species", "(any)")
    species_clause = _species_clause(species)

    if not non_empty:
        # Date-only search (no text filter)
        return f"{date} AND {species_clause}" if species_clause else date

    group_clauses = [_group_to_lucene(g) for g in non_empty]
    if len(group_clauses) == 1:
        text_part = group_clauses[0]
    else:
        text_part = " OR ".join(f"({c})" for c in group_clauses)

    base = f"({text_part}) AND {date}"
    return f"({base}) AND {species_clause}" if species_clause else base


def build_psyarxiv_query(filter_dict: Dict[str, Any]) -> str:
    """
    Build a plain keyword query for PsyArXiv (OSF API).
    OSF doesn't support Lucene — returns a space-joined keyword string.
    """
    groups = filter_dict.get("text_groups", [])
    if not groups and filter_dict.get("keywords"):
        groups = [{"title": "", "abstract": "", "both": ", ".join(filter_dict["keywords"])}]

    all_terms: List[str] = []
    for g in groups:
        for field in ("title", "abstract", "both"):
            for t in _split_terms(g.get(field, "")):
                all_terms.extend(and_parts(t))

    # Remove duplicates (case-insensitive), strip wildcards for plain-text search
    seen: set = set()
    unique: List[str] = []
    for t in all_terms:
        clean = t.rstrip("*")
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            unique.append(clean)

    return " ".join(unique)


def _osf_title_part(term: str) -> str:
    """What one title term sends as filter[title]. For "a AND b" that is the
    longest part: any title containing every part contains it, so nothing is
    lost, and the local filter checks the rest (AND5)."""
    parts = [p.rstrip("*").strip() for p in and_parts(term)]
    parts = [p for p in parts if p]
    return max(parts, key=len) if parts else ""


def osf_title_terms(filter_dict: Dict[str, Any], max_terms: int) -> List[str]:
    """Title terms to send to an OSF provider as filter[title], or [] for none.

    Purpose: Narrow OSF requests by title only where that cannot lose a match.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B3
    Tests:   tests/test_adapters.py::test_b3_every_or_group_reaches_osf,
             tests/test_adapters.py::test_b3_group_without_title_term_fetches_by_date

    Groups are OR'd, and within a group every non-empty field must match. A
    group with a title term therefore only matches papers whose title contains
    one of its title terms. When every group has one, the union of the title
    terms covers every possible match. When any group has none (or there are
    more distinct terms than max_terms), return [] so the caller fetches by
    date alone. Multi-word terms are kept whole; a trailing wildcard is
    dropped, since filter[title] is already a contains-match. An AND term
    sends its longest part (AND5).
    """
    groups = filter_dict.get("text_groups") or []
    non_empty = [g for g in groups
                 if _has_text(g)]
    if not non_empty:
        return []
    terms: List[str] = []
    seen: set = set()
    for g in non_empty:
        title_terms = [_osf_title_part(t) for t in _split_terms(g.get("title") or "")]
        title_terms = [t for t in title_terms if t]
        if not title_terms:
            return []
        for t in title_terms:
            if t.lower() not in seen:
                seen.add(t.lower())
                terms.append(t)
    if len(terms) > max_terms:
        logger.info("OSF: %d title terms is over the limit of %d; fetching by date only",
                    len(terms), max_terms)
        return []
    return terms


def get_date_range(filter_dict: Dict[str, Any]) -> tuple:
    """Return (start_date, end_date) strings from a filter_dict.

    Purpose: One date window for every source that takes a date range.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B2
    Tests:   tests/test_query_builder.py::test_b2_empty_string_dates_use_days_back_arxiv,
             tests/test_query_builder.py::test_b2_empty_string_dates_use_days_back_osf

    An empty or None date means "not set", as in _date_clause: saved filters
    and the web editor store "" for an unused date.
    """
    days_back = filter_dict.get("days_back", 7) or 0
    end_date = filter_dict.get("end_date") or datetime.today().strftime("%Y-%m-%d")
    start_date = (filter_dict.get("start_date")
                  or (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d"))
    return start_date, end_date


def build_arxiv_query(filter_dict: Dict[str, Any]) -> str:
    """
    Build an arXiv query string from a filter_dict.

    arXiv query syntax:
    - Field prefixes: ti: (title), abs: (abstract), all: (both), au: (author)
    - Phrases: "exact phrase"
    - Boolean: AND, OR, NOT
    - Date: submittedDate:[YYYYMMDDHHmm TO YYYYMMDDHHmm]

    Returns a query string ready for the arXiv API's search_query param.
    """
    groups = filter_dict.get("text_groups", [])
    if not groups and filter_dict.get("keywords"):
        groups = [{"title": "", "abstract": "", "both": ", ".join(filter_dict["keywords"])}]

    # Build text clauses from groups (OR-joined)
    group_clauses: List[str] = []
    for g in groups:
        group_clause = _group_to_arxiv(g)
        if group_clause:
            group_clauses.append(group_clause)

    # Author filtering is intentionally omitted from the arXiv query. arXiv's
    # au:"…" phrase clause is stricter than the client-side substring match
    # (decision M3), so applying it at query time returns fewer results than
    # the saved filter intends. The client-side filter_papers() owns author
    # matching consistently across all sources.

    # Build date range clause
    start_date, end_date = get_date_range(filter_dict)
    # arXiv date format: YYYYMMDDHHmm
    start_ymd = start_date.replace("-", "") + "0000"
    end_ymd = end_date.replace("-", "") + "2359"
    date_clause = f"submittedDate:[{start_ymd} TO {end_ymd}]"

    # Combine all parts
    parts: List[str] = []

    # Add text groups (OR-joined)
    if group_clauses:
        if len(group_clauses) > 1:
            text_part = " OR ".join(f"({c})" for c in group_clauses)
        else:
            text_part = group_clauses[0]
        parts.append(f"({text_part})")

    # Add date (always present)
    parts.append(date_clause)

    # Join all parts with AND
    return " AND ".join(parts)


def _arxiv_clause(term: str, prefix: str) -> str:
    """One comma-separated term for arXiv: ti:x, or (ti:a AND ti:b) for an
    AND term; wildcards stripped per part (AND4)."""
    parts = [p for p in and_parts(term) if p.rstrip("*")]
    # arXiv's own wildcard is partial (TD3, live 2026-10-07: all:cooperati* 50,
    # all:cooperation 3,273), so inside an AND term a wildcard part is left
    # out when another part can narrow the query; the local filter checks
    # every part. Alone, the wildcard is sent as written (stripped it was a
    # non-word that found nothing).
    plain = [p for p in parts if not p.endswith("*")]
    if plain and len(plain) < len(parts):
        parts = plain
    clauses = [_term_clause(p, prefix) for p in parts]
    if not clauses:
        return ""
    return clauses[0] if len(clauses) == 1 else "(" + " AND ".join(clauses) + ")"


def _group_to_arxiv(group: Dict[str, str]) -> str:
    """Convert one text_group to an arXiv query clause (title/abstract/both).

    Note: arXiv's `all:` field is broader than Europe PMC's bare term — it also
    matches authors, journal-ref, and comments, not just title and abstract.
    Users never see those extra hits: every result is filtered again on title
    and abstract (filter_papers, in routes_searches and monitor.py). Their
    only cost is that they count against arXiv's Max results before they are
    dropped (plan 2026-09-29 T3.6).
    """
    title_terms    = _split_terms(group.get("title",    ""))
    abstract_terms = _split_terms(group.get("abstract", ""))
    both_terms     = _split_terms(group.get("both",     ""))

    parts: List[str] = []

    for prefix, terms in (("ti", title_terms), ("abs", abstract_terms), ("all", both_terms)):
        clauses = [c for c in (_arxiv_clause(t, prefix) for t in terms) if c]
        if clauses:
            parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if not parts:
        return ""

    return " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else parts[0]
