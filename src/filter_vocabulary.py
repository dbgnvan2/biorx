"""
The filter vocabulary: which options each facet offers and what they match.

Purpose: One source for facet ids, labels and match rules, read by the search
         code and served to the web page, so no front end keeps its own copy.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S3, #B5
Tests:   tests/test_filter_vocabulary.py

The options live in filter_vocabulary.yaml (editorial content, not code).
Filters store option ids. Values saved by earlier versions (display labels
such as "review article") are mapped to ids by normalise_value, so stored
filters keep working without being re-saved.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VOCAB_PATH = Path(__file__).resolve().parent.parent / "filter_vocabulary.yaml"

# Facets stored on a filter under the same key.
FACETS = ("paper_type", "version", "published", "license", "species", "category")

ANY = "any"


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    """Read filter_vocabulary.yaml once. A missing or broken file is an error:
    without it no facet can be matched, and guessing would change results."""
    import yaml
    with open(VOCAB_PATH) as fh:
        data = yaml.safe_load(fh) or {}
    missing = [f for f in FACETS if f not in data]
    if missing:
        raise ValueError(f"{VOCAB_PATH.name} has no entry for: {', '.join(missing)}")
    return data


def options(facet: str) -> List[Dict[str, Any]]:
    return list(load()[facet].get("options") or [])


def option_ids(facet: str) -> List[str]:
    return [str(o["id"]) for o in options(facet)]


class UnknownValue(ValueError):
    """A facet value that is neither an option id nor a known legacy value."""


def normalise_value(facet: str, value: Any) -> Optional[str]:
    """Return the option id for a stored or submitted facet value.

    Returns ANY for an empty value, the id itself for an id, the mapped id for
    a legacy value, and None for a legacy value that has no equivalent any
    more. Raises UnknownValue for anything else.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return ANY
    v = str(value).strip()
    if v in option_ids(facet):
        return v
    legacy = load()[facet].get("legacy") or {}
    if v in legacy:
        mapped = legacy[v]
        return None if mapped is None else str(mapped)
    raise UnknownValue(f"{facet}: {v!r} is not one of {', '.join(option_ids(facet))}")


def problems(filter_dict: Dict[str, Any]) -> List[str]:
    """Reasons a filter cannot be saved as it is (empty when it can).

    Purpose: Refuse facet values the search cannot apply, at save time.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S3, #B5
    Tests:   tests/web/test_filters_routes.py::test_s3_unknown_facet_value_refused,
             tests/web/test_filters_routes.py::test_b5_institution_refused
    """
    out: List[str] = []
    for facet in FACETS:
        if facet not in filter_dict:
            continue
        try:
            normalise_value(facet, filter_dict[facet])
        except UnknownValue as exc:
            out.append(str(exc))
    inst = filter_dict.get("institution")
    if isinstance(inst, (list, tuple)):
        inst = ", ".join(str(i) for i in inst)
    if inst and str(inst).strip():
        out.append("institution: no source reports author institutions, so this "
                   "filter would match nothing; remove it")
    return out


def for_client() -> Dict[str, List[Dict[str, str]]]:
    """The options per facet, as the web page needs them (id and label)."""
    return {f: [{"id": str(o["id"]), "label": str(o["label"])} for o in options(f)]
            for f in FACETS}


# ── Matching ──────────────────────────────────────────────────────────────────

def document_type_for(source_types: List[str]) -> str:
    """Map a source's publication types to document_type (article, review,
    trial, preprint or other), by the rules in filter_vocabulary.yaml.

    Purpose: Give every record a document_type the paper-type facet can match.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B5
    Tests:   tests/test_adapters.py::test_b5_europepmc_types_from_real_response
    """
    lowered = [str(t).lower() for t in source_types if t]
    for rule in load()["paper_type"].get("source_types") or []:
        if any(word in t for t in lowered for word in rule.get("contains") or []):
            return str(rule["document_type"])
    return "other"


def paper_type_matches(type_id: str, document_type: str) -> bool:
    for o in options("paper_type"):
        if str(o["id"]) == type_id:
            return (document_type or "").lower() in [str(m) for m in (o.get("match") or [])]
    return False


_CC_RE = re.compile(r"\bcc-?by((?:-(?:nc|nd|sa))*)\b")
_CC_URL_RE = re.compile(r"creativecommons\.org/licenses/([a-z-]+)")
_CC_ORDER = ("nc", "nd", "sa")
_CC_WORDS = (("noncommercial", "nc"), ("noderiv", "nd"), ("sharealike", "sa"))


def license_id(value: str) -> str:
    """Reduce a source's licence string to a vocabulary id, or "" if unknown.

    Purpose: Compare licences by meaning, not spelling.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B5
    Tests:   tests/test_filter_vocabulary.py::test_b5_license_forms_normalise

    Handles bioRxiv ("cc_by_nc"), Europe PMC ("cc by-nc"), Unpaywall
    ("cc-by-nc", "public-domain"), OSF ("CC-By Attribution 4.0 International",
    "CC0 1.0 Universal") and Creative Commons URLs.
    """
    s = (value or "").strip().lower()
    if not s:
        return ""
    aliases = {str(k).lower(): str(v) for k, v in
               (load()["license"].get("aliases") or {}).items()}
    if s in aliases:
        return aliases[s]
    if "publicdomain/zero" in s or re.search(r"\bcc-?0\b|\bcc ?zero\b", s.replace("_", " ")):
        return "cc0"
    if "publicdomain/mark" in s:
        return "pd"
    url = _CC_URL_RE.search(s)
    if url:
        s = "cc-" + url.group(1).strip("-")
    flat = re.sub(r"[\s_]+", "-", s)
    m = _CC_RE.search(flat)
    if m:
        parts = {p for p in m.group(1).split("-") if p}
        # OSF spells the conditions out: "CC-By Attribution-NonCommercial-
        # NoDerivatives 4.0 International".
        squashed = flat.replace("-", "")
        for word, code in _CC_WORDS:
            if word in squashed:
                parts.add(code)
        return "cc-by" + "".join(f"-{p}" for p in _CC_ORDER if p in parts)
    return aliases.get(flat, "")


def excluded_organisms() -> List[str]:
    return [str(o) for o in (load()["species"].get("excluded_organisms") or [])]


@lru_cache(maxsize=1)
def animal_title_pattern() -> Optional["re.Pattern[str]"]:
    """Purpose: One compiled test for "this title names an animal study".
    Spec:    docs/cycles/2026-09-29_browser-run.md (species re-check)
    Tests:   tests/test_filtering.py::test_br7_no_animal_drops_animal_titles

    Whole words or phrases from species.animal_title_terms, any case. None
    when the list is empty (then nothing is excluded here).
    """
    terms = [str(t).strip() for t in (load()["species"].get("animal_title_terms") or [])
             if str(t).strip()]
    if not terms:
        return None
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=1)
def animal_title_exceptions() -> Optional["re.Pattern[str]"]:
    """species.animal_title_exceptions as one pattern ("mouse tracking",
    "Chinese hamster ovary"): phrases in which an animal word is not the
    animal studied."""
    phrases = [str(t).strip() for t in (load()["species"].get("animal_title_exceptions") or [])
               if str(t).strip()]
    if not phrases:
        return None
    return re.compile("|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)),
                      re.IGNORECASE)


def title_names_an_animal_study(title: str) -> bool:
    """Whether a title names an animal study. Exception phrases are taken out
    first; a match in capitals is an acronym, not the animal ("MICE" is
    multiple imputation by chained equations). QA gate 2026-09-29."""
    pattern = animal_title_pattern()
    if not pattern:
        return False
    title = title or ""
    exceptions = animal_title_exceptions()
    if exceptions:
        title = exceptions.sub(" ", title)
    return any(not (m.group(0).isupper() and len(m.group(0)) > 1)
               for m in pattern.finditer(title))
