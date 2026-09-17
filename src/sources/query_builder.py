"""
Convert a filter_dict (from the GUI) into source-specific query strings.

filter_dict text_groups structure:
    [{"title": "stress,cortisol", "abstract": "", "both": "adolescen*"}, ...]

Groups are OR-joined. Within a group:
  - title terms  → must appear in title (AND between non-empty fields, OR within a field)
  - abstract terms → must appear in abstract
  - both terms   → must appear in title OR abstract

Wildcard: term ending in '*' = prefix match (works natively in Europe PMC Lucene and
  in Europe PMC's bare term matches — but NOT in arXiv, which has no wildcard operator).
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


# ── Internal helpers ────────────────────────────────────────────────────────────

def _split_terms(s: str) -> List[str]:
    """Split comma-separated field into non-empty stripped terms."""
    return [t.strip() for t in s.split(",") if t.strip()]


def _lucene_term(term: str, field: str = "") -> str:
    """Wrap a single term with an optional Lucene field prefix."""
    # Lucene-escape special chars except * (wildcard) and trailing *
    safe = term.replace('"', '\\"')
    if field:
        return f'{field}:"{safe}"' if " " in safe.rstrip("*") else f"{field}:{safe}"
    return f'"{safe}"' if " " in safe.rstrip("*") else safe


def _group_to_lucene(group: Dict[str, str]) -> str:
    """Convert one AND-group dict to a Lucene clause string."""
    title_terms    = _split_terms(group.get("title",    ""))
    abstract_terms = _split_terms(group.get("abstract", ""))
    both_terms     = _split_terms(group.get("both",     ""))

    parts: List[str] = []

    if title_terms:
        clauses = [_lucene_term(t, "TITLE") for t in title_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if abstract_terms:
        clauses = [_lucene_term(t, "ABSTRACT") for t in abstract_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if both_terms:
        # "both" = any field — bare term matches title OR abstract in Europe PMC
        clauses = [_lucene_term(t) for t in both_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if not parts:
        return ""

    return " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else parts[0]


_ANIMAL_ORGANISM_EXCLUSIONS = (
    "NOT ANIMAL:y "
    'NOT ORGANISM:"Mus musculus" '
    'NOT ORGANISM:"Rattus norvegicus" '
    'NOT ORGANISM:Mouse '
    'NOT ORGANISM:Rat '
    'NOT ORGANISM:Zebrafish '
    'NOT ORGANISM:"Danio rerio" '
    'NOT ORGANISM:"Drosophila melanogaster" '
    'NOT ORGANISM:"Caenorhabditis elegans" '
    'NOT ORGANISM:"Macaca mulatta"'
)


def _species_clause(species: str) -> str:
    """Return a Europe PMC Lucene clause for the study-type / species filter.

    ANIMAL:y is a curated Europe PMC flag but is incomplete — many animal studies
    lack it.  We supplement with explicit ORGANISM exclusions for the most common
    model organisms so that untagged rodent/fish/fly papers are also excluded.
    """
    if species in ("Human studies only", "Exclude animal studies"):
        return _ANIMAL_ORGANISM_EXCLUSIONS
    if species == "Animal studies only":
        return "ANIMAL:y"
    return ""  # "(any)" → no constraint


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
        if any(g.get(k, "").strip() for k in ("title", "abstract", "both"))
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
            all_terms.extend(_split_terms(g.get(field, "")))

    # Remove duplicates, strip wildcards for plain-text search
    seen = set()
    unique: List[str] = []
    for t in all_terms:
        clean = t.rstrip("*")
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)

    return " ".join(unique)


def get_date_range(filter_dict: Dict[str, Any]) -> tuple:
    """Return (start_date, end_date) strings from a filter_dict."""
    days_back = filter_dict.get("days_back", 7)
    end_date  = filter_dict.get("end_date",  datetime.today().strftime("%Y-%m-%d"))
    start_date = filter_dict.get(
        "start_date",
        (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    )
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


def _strip_arxiv_wildcard(term: str) -> str:
    """arXiv has no wildcard operator; strip trailing * and log when removed."""
    clean = term.rstrip("*")
    if clean != term:
        logger.debug("arXiv query: stripped wildcard from %r → %r", term, clean)
    return clean


def _group_to_arxiv(group: Dict[str, str]) -> str:
    """Convert one text_group to an arXiv query clause (title/abstract/both).

    Note: arXiv's `all:` field is broader than Europe PMC's bare term — it also
    matches authors, journal-ref, and comments, not just title and abstract.
    This is defensible but currently undocumented to users (see TODO.md).
    """
    title_terms    = [_strip_arxiv_wildcard(t) for t in _split_terms(group.get("title",    ""))]
    abstract_terms = [_strip_arxiv_wildcard(t) for t in _split_terms(group.get("abstract", ""))]
    both_terms     = [_strip_arxiv_wildcard(t) for t in _split_terms(group.get("both",     ""))]

    parts: List[str] = []

    if title_terms:
        clauses = [f'ti:"{t}"' if " " in t else f"ti:{t}" for t in title_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if abstract_terms:
        clauses = [f'abs:"{t}"' if " " in t else f"abs:{t}" for t in abstract_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if both_terms:
        clauses = [f'all:"{t}"' if " " in t else f"all:{t}" for t in both_terms]
        parts.append("(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if not parts:
        return ""

    return " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else parts[0]
