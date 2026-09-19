"""
Purpose: Find a free full-text copy of a paper, so summaries are made from the
         paper rather than its abstract.
Spec:    docs/implementation_plan_2026-09-19_full_text.md#C2 (FT3)
Tests:   tests/test_fulltext.py

Order, stopping at the first PDF that downloads and has extractable text:
  1. the paper's own links (pdf_url / best_oa_url / publisher);
  2. Unpaywall by DOI;
  3. OpenAlex by DOI, else by title — every open-access location it knows,
     including repository and author copies;
  4. Semantic Scholar by DOI, else by title — its openAccessPdf.

Google Scholar and ResearchGate are deliberately absent: neither has a public
API and both forbid automated access (plan C3).

A title search can find the wrong paper, and a summary of the wrong paper is
worse than none. A title hit is accepted only when the normalised titles are
equal AND the first author or the year also matches (title_matches).

A finder that cannot be reached is recorded as unreachable, never as "had no
copy" (learnings P1/P35): the difference decides whether trying again helps.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

from .sources.base import with_retry
from .sources.errors import RateLimitedError, SourceUnavailableError

logger = logging.getLogger(__name__)

OPENALEX_WORKS = "https://api.openalex.org/works"
S2_PAPER = "https://api.semanticscholar.org/graph/v1/paper"
_S2_FIELDS = "title,year,authors,openAccessPdf"
_OPENALEX_SELECT = "title,publication_year,authorships,best_oa_location,locations,open_access"
HTTP_TIMEOUT = 15

# download(url) -> extracted text, "" when the file had none. Raises
# NoText(reason) when the URL did not give a usable PDF.
Download = Callable[[str], str]
# get_json(url, params) -> dict, or None for "not found". Raises
# SourceUnavailableError when the service could not be asked.
GetJson = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]


class NoText(Exception):
    """This URL gave no usable text; the message says why, for the user."""


@dataclass
class FullText:
    text: str = ""
    source: str = ""            # e.g. "OpenAlex (by title)"
    url: str = ""
    tried: List[str] = field(default_factory=list)      # "Unpaywall: no free copy"
    unreachable: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.text)

    def explain(self) -> str:
        """One line for the user: where it looked and what each place said."""
        return "; ".join(self.tried) or "nowhere to look"


# ── Matching ─────────────────────────────────────────────────────────────────

def normalise_title(title: str) -> str:
    """Case, accents, punctuation and spacing removed, "&" read as "and":
    "Stress & the Brain:" -> "stress and the brain"."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace("&", " and ")          # publishers write the same title both ways
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _name_tokens(name: str) -> set:
    """Surname-ish tokens of a name in either order: "Smith J" / "John Smith"."""
    return {t for t in normalise_title(name).split() if len(t) > 1}


def _first_author(paper: Dict[str, Any]) -> str:
    authors = paper.get("authors") or ""
    if isinstance(authors, list):
        first = authors[0] if authors else ""
        return first.get("display_name", "") if isinstance(first, dict) else str(first)
    return re.split(r"[;,]", str(authors))[0] if authors else ""


def _year(value: Any) -> str:
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(value or ""))
    return m.group(1) if m else ""


def title_matches(paper: Dict[str, Any], title: str, first_author: str, year: Any) -> bool:
    """Purpose: Decide whether a title-search hit is this paper.
    Spec:    docs/implementation_plan_2026-09-19_full_text.md#C2
    Tests:   tests/test_fulltext.py::test_ft3_2_near_miss_title_is_rejected,
             tests/test_fulltext.py::test_ft3_3_title_alone_is_not_enough

    Equal normalised titles, AND a shared first-author surname or the same year.
    A paper with neither author nor year cannot be confirmed, so it is refused.
    """
    want = normalise_title(paper.get("title", ""))
    if not want or want != normalise_title(title):
        return False
    ours = _name_tokens(_first_author(paper))
    if ours and ours & _name_tokens(first_author):
        return True
    our_year = _year(paper.get("year") or paper.get("pub_date") or paper.get("published_date"))
    return bool(our_year) and our_year == _year(year)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def default_get_json(user_agent: str) -> GetJson:
    """GET a JSON document with timeout + retry + backoff (standards: P5).
    404 -> None; 429 and exhausted retries -> SourceUnavailableError."""
    def get(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        def call():
            resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                                headers={"User-Agent": user_agent})
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp
        resp = with_retry(call, source_label=url.split("/")[2])
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            raise RateLimitedError(f"{url.split('/')[2]} is rate-limiting requests")
        if not resp.ok:
            raise SourceUnavailableError(f"{url.split('/')[2]} returned {resp.status_code}")
        try:
            return resp.json()
        except ValueError as e:
            raise SourceUnavailableError(f"{url.split('/')[2]} returned non-JSON") from e
    return get


# ── Finders: each returns candidate PDF URLs, best first ─────────────────────

def _clean_doi(doi: str) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", (doi or "").strip(), flags=re.I)


def unpaywall_urls(paper: Dict[str, Any], email: str, get_json: GetJson) -> List[str]:
    doi = _clean_doi(paper.get("doi", ""))
    if not doi or not email:
        return []
    data = get_json(f"https://api.unpaywall.org/v2/{doi}", {"email": email})
    if not data:
        return []
    locs = [data.get("best_oa_location") or {}] + list(data.get("oa_locations") or [])
    return [l.get("url_for_pdf") for l in locs if l and l.get("url_for_pdf")]


def _openalex_work_urls(work: Dict[str, Any]) -> List[str]:
    urls = [(work.get("best_oa_location") or {}).get("pdf_url")]
    urls += [(loc or {}).get("pdf_url") for loc in work.get("locations") or []]
    urls.append((work.get("open_access") or {}).get("oa_url"))
    return [u for u in urls if u]


def openalex_urls(paper: Dict[str, Any], by_title: bool, email: str,
                  get_json: GetJson) -> List[str]:
    params = {"select": _OPENALEX_SELECT}
    if email:
        params["mailto"] = email
    doi = _clean_doi(paper.get("doi", ""))
    if doi:
        work = get_json(f"{OPENALEX_WORKS}/doi:{doi}", params)
        if work:
            return _openalex_work_urls(work)
    if not by_title or not paper.get("title"):
        return []
    words = normalise_title(paper["title"])
    data = get_json(OPENALEX_WORKS, {**params, "filter": f"title.search:{words}",
                                     "per-page": 5})
    urls: List[str] = []
    for work in (data or {}).get("results") or []:
        first = ((work.get("authorships") or [{}])[0].get("author") or {}).get("display_name", "")
        if title_matches(paper, work.get("title", ""), first, work.get("publication_year")):
            urls += _openalex_work_urls(work)
        else:
            logger.info("OpenAlex title hit rejected (not the same paper): %r",
                        (work.get("title") or "")[:80])
    return urls


def semantic_scholar_urls(paper: Dict[str, Any], by_title: bool,
                          get_json: GetJson) -> List[str]:
    fields = {"fields": _S2_FIELDS}
    doi = _clean_doi(paper.get("doi", ""))
    if doi:
        hit = get_json(f"{S2_PAPER}/DOI:{doi}", fields)
        if hit:
            url = (hit.get("openAccessPdf") or {}).get("url")
            return [url] if url else []
    if not by_title or not paper.get("title"):
        return []
    data = get_json(f"{S2_PAPER}/search/match", {**fields, "query": paper["title"]})
    urls = []
    for hit in (data or {}).get("data") or []:
        first = ((hit.get("authors") or [{}])[0] or {}).get("name", "")
        if title_matches(paper, hit.get("title", ""), first, hit.get("year")):
            url = (hit.get("openAccessPdf") or {}).get("url")
            if url:
                urls.append(url)
        else:
            logger.info("Semantic Scholar title hit rejected (not the same paper): %r",
                        (hit.get("title") or "")[:80])
    return urls


# ── The chain ────────────────────────────────────────────────────────────────

def find_full_text(paper: Dict[str, Any], download: Download, *,
                   own_links: List[str], by_title: bool = True, email: str = "",
                   max_downloads: int = 4,
                   get_json: Optional[GetJson] = None,
                   user_agent: str = "biorx/1.0") -> FullText:
    """Purpose: Try each place in turn for a PDF with extractable text.
    Spec:    docs/implementation_plan_2026-09-19_full_text.md#C2
    Tests:   tests/test_fulltext.py::test_ft3_1_chain_order_and_stop,
             tests/test_fulltext.py::test_ft3_6_outage_is_not_absence

    max_downloads caps how many PDFs are fetched per paper (each can take up to
    30 s); what the cap skipped is reported, not dropped silently (P9).
    """
    get_json = get_json or default_get_json(user_agent)
    result = FullText()
    seen: set = set()
    downloads = 0

    finders = [
        ("the paper's own link", lambda: list(own_links)),
        ("Unpaywall", lambda: unpaywall_urls(paper, email, get_json) if email else None),
        ("OpenAlex", lambda: openalex_urls(paper, by_title, email, get_json)),
        ("Semantic Scholar", lambda: semantic_scholar_urls(paper, by_title, get_json)),
    ]
    for name, find in finders:
        try:
            urls = find()
        except (SourceUnavailableError, RateLimitedError, requests.RequestException) as e:
            result.unreachable.append(name)
            result.tried.append(f"{name}: could not be reached")
            logger.info("Full-text finder %s unreachable: %s", name, e)
            continue
        if urls is None:
            result.tried.append(f"{name}: skipped (no contact email set)")
            continue
        urls = [u for u in urls if u and u not in seen]
        if not urls:
            result.tried.append(f"{name}: no free copy")
            continue
        reasons = []
        for url in urls:
            seen.add(url)
            if downloads >= max_downloads:
                reasons.append(f"not downloaded (limit of {max_downloads} PDFs reached)")
                break
            downloads += 1
            try:
                text = download(url)
            except NoText as e:
                reasons.append(str(e))
                continue
            if text.strip():
                result.text, result.url = text, url
                result.source = name
                result.tried.append(f"{name}: full text found")
                return result
            reasons.append("the PDF had no extractable text")
        result.tried.append(f"{name}: " + "; ".join(dict.fromkeys(reasons)))
    return result
