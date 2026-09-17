"""
Purpose: Resolve a paper's PDF / landing-page URL and recover a missing abstract.
Spec:    docs/implementation_plan_2026-09-15.md#2.1
Tests:   tests/test_paper_meta.py

Extracted verbatim from gui.py so the web backend and the CLI can reuse it
without importing PyQt6. Behaviour is unchanged; the only edits are hoisting
function-local imports to module level and promoting the timeouts and contact
string to named constants (external-api E1, learnings P4).
"""

from __future__ import annotations

import json
import logging
import os
import re
from html.parser import HTMLParser
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Named rather than inline so they are tunable and greppable (E1, P4).
SCRAPE_TIMEOUT = 20
OPENALEX_TIMEOUT = 15
MIN_ABSTRACT_CHARS = 80          # below this, a "description" is a site tagline
MIN_HTML_CHARS = 500             # below this, the response is an error/interstitial
def openalex_user_agent() -> str:
    """User-Agent for the OpenAlex polite pool, read at call time.

    One source for every polite-pool header (src/sources/config.py): env
    BIORX_CONTACT_EMAIL wins; falls back to contact_email in sources_config.yaml.
    """
    from src.sources.config import load_sources_config, polite_user_agent
    try:
        cfg = load_sources_config()
    except Exception:
        cfg = {}
    return polite_user_agent(cfg)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── URLs ──────────────────────────────────────────────────────────────────────

def pdf_url(paper: Dict[str, Any]) -> str:
    """Return best URL for opening a paper's PDF or landing page."""
    # Prefer an explicit PDF/OA URL from enrichment
    if paper.get("pdf_url"):
        return paper["pdf_url"]
    if paper.get("best_oa_url"):
        return paper["best_oa_url"]
    # bioRxiv-style fallback
    doi     = paper.get("doi", "")
    version = paper.get("version", "1")
    source  = paper.get("source", paper.get("server", ""))
    if doi and source in ("biorxiv_medrxiv", "biorxiv", "medrxiv"):
        server = "biorxiv" if "biorxiv" in source else "medrxiv"
        return f"https://www.{server}.org/content/{doi}v{version}.full.pdf"
    if doi:
        return f"https://doi.org/{doi}"
    return paper.get("source_url", paper.get("url", ""))


def paper_link(paper: Dict[str, Any]) -> str:
    """Return the best landing-page/document URL for a paper.

    Prefers the publisher/source landing page, then a DOI resolver, then any
    explicit PDF/OA URL. Used for export links the user can click through to
    the document of record.
    """
    doi = paper.get("doi", "")
    return (
        paper.get("source_url")
        or paper.get("url")
        or (f"https://doi.org/{doi}" if doi else "")
        or pdf_url(paper)
    )


# ── Abstract recovery ─────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)


def scrape_abstract_from_url(url: str) -> str:
    """
    Attempt to scrape an abstract from a publisher page.

    Tries in order:
      1. JSON-LD structured data (schema.org/ScholarlyArticle description)
      2. <meta name="description"> / og:description
      3. Common HTML patterns: section/div with id or class containing "abstract"
    Returns empty string if nothing useful is found or the page is paywalled.
    """
    try:
        resp = requests.get(
            url, headers=_BROWSER_HEADERS, timeout=SCRAPE_TIMEOUT, allow_redirects=True
        )
        if not resp.ok or len(resp.text) < MIN_HTML_CHARS:
            return ""
        html = resp.text
    except Exception as e:
        logger.debug("Abstract scrape fetch failed for %s: %s", url, e)
        return ""

    # 1. JSON-LD structured data
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(m.group(1))
            # May be a list or a single object
            items = data if isinstance(data, list) else [data]
            for item in items:
                # Check top-level and mainEntity (Springer/BMC wraps in WebPage > mainEntity)
                candidates = [item, item.get("mainEntity") or {}]
                for candidate in candidates:
                    desc = candidate.get("description") or candidate.get("abstract") or ""
                    if desc and len(desc) > MIN_ABSTRACT_CHARS:
                        return re.sub(r"<[^>]+>", "", desc).strip()
        except Exception:
            continue

    # 2. Meta tags
    for pattern in [
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+name=["\']citation_abstract["\']\s+content=["\'](.*?)["\']',
    ]:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = m.group(1).strip()
            if len(text) > MIN_ABSTRACT_CHARS:
                return re.sub(r"<[^>]+>", "", text).strip()

    # 3. HTML section/div with abstract id or class
    for pattern in [
        r'<(?:section|div|p)[^>]+(?:id|class)=["\'][^"\']*\babstract\b[^"\']*["\'][^>]*>(.*?)</(?:section|div)',
        r'id=["\']Abs1[^"\']*["\'][^>]*>(.*?)</section',
        r'id=["\']abstract["\'][^>]*>(.*?)</(?:section|div)',
    ]:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = re.sub(r"<[^>]+>", " ", m.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > MIN_ABSTRACT_CHARS:
                return text

    return ""


def fetch_openalex_abstract(doi: str) -> str:
    """
    Fetch abstract from OpenAlex via its inverted-index format.
    OpenAlex stores abstracts as {word: [position, ...]} dicts to work around
    publisher restrictions; we reconstruct the plain text here.
    Returns empty string on any failure.
    """
    try:
        clean = re.sub(r"^https?://doi\.org/", "", doi.strip())
        resp = requests.get(
            f"https://api.openalex.org/works/doi:{clean}",
            params={"select": "abstract_inverted_index"},
            headers={"User-Agent": openalex_user_agent()},
            timeout=OPENALEX_TIMEOUT,
        )
        if not resp.ok:
            return ""
        idx = resp.json().get("abstract_inverted_index") or {}
        if not idx:
            return ""
        # Reconstruct: sort (position, word) pairs and join
        pairs = sorted(
            (pos, word)
            for word, positions in idx.items()
            for pos in positions
        )
        return " ".join(word for _, word in pairs)
    except Exception as e:
        logger.debug("OpenAlex abstract fetch failed for %s: %s", doi, e)
        return ""


# ── Recovering a missing abstract ─────────────────────────────────────────────

# Exceptions that mean a bug in this code rather than a failing service.
_OUR_BUGS = (NameError, TypeError, AttributeError, ImportError, SyntaxError)

_PMCID_IN_URL = re.compile(r"\b(PMC\d+)\b", re.IGNORECASE)


@dataclass
class AbstractRecovery:
    """The outcome of looking for a missing abstract.

    `found` is False and `text` is "" when nothing was recovered. The desktop
    worker used to emit "(Abstract not available from any source)" as its
    *success* value — text that could be stored or summarized as if it were an
    abstract (learnings P14). A failure is now a state, not a string.
    """
    text: str = ""
    source: Optional[str] = None
    tried: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.text)


def pmcid_of(paper: Dict[str, Any]) -> str:
    """The paper's PMCID, from the record or from a PMC link it carries."""
    pmcid = (paper.get("pmcid") or "").strip()
    if pmcid:
        return pmcid.upper() if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    for key in ("best_oa_url", "source_url", "url", "pdf_url"):
        match = _PMCID_IN_URL.search(paper.get(key) or "")
        if match:
            return match.group(1).upper()
    return ""


def _europepmc():
    from .sources.europepmc import EuropePmcAdapter
    return EuropePmcAdapter()


def _crossref_abstract(doi: str) -> str:
    """Crossref sometimes carries a JATS abstract that Europe PMC lacks."""
    from .sources.crossref import CrossrefAdapter
    from .sources.schema import CanonicalRecord, RecordFlags, make_canonical_id

    record = CanonicalRecord(
        canonical_id=make_canonical_id(doi=doi, title="", first_author="", year=0),
        title="", abstract="", authors=[], year=0, published_date="",
        document_type="article", is_preprint=False, journal_or_server="", doi=doi,
        pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="", license="",
        oa_status="", subjects=[], keywords=[], source_hits=[], flags=RecordFlags(),
    )
    CrossrefAdapter().enrich(record)
    return record.abstract or ""


def recover_abstract(paper: Dict[str, Any]) -> AbstractRecovery:
    """Find an abstract for a paper whose record has none.

    Spec:  docs/implementation_plan_2026-09-16_backlog.md#N2
    Tests: tests/test_n2_abstract_recovery.py

    Tried in order, stopping at the first that yields text:
      1. Europe PMC by DOI       — most journal papers; may also reveal a PMCID
      2. PMC full-text XML       — open-access papers, by PMCID from the record,
                                   the DOI lookup, or a PMC link
      3. Crossref by DOI         — sometimes has a JATS abstract
      4. OpenAlex by DOI         — reconstructed from its inverted index
      5. The paper's own pages   — open-access link, landing page, DOI resolver

    Moved from gui.py's AbstractFetchWorker so the desktop app and the web app
    use one implementation. Two defects fixed in the move: step 2 ran only when
    the paper had a DOI, so a PMCID-only paper never reached PMC; and the open-
    access link was never scraped.

    A source that raises is logged and skipped, never allowed to end the chain,
    so one flaky service cannot hide an abstract another one has (P5).
    """
    result = AbstractRecovery()
    doi = (paper.get("doi") or "").strip()
    pmcid = pmcid_of(paper)

    def attempt(name: str, fn) -> bool:
        result.tried.append(name)
        try:
            text = (fn() or "").strip()
        except _OUR_BUGS as e:
            # Isolation is for unreliable services, not for defects in this
            # code: a NameError or TypeError here must be visible, with a
            # traceback, rather than read as "the source had nothing".
            logger.error("Abstract recovery: %s raised a programming error: %s",
                         name, e, exc_info=True)
            return False
        except Exception as e:
            logger.info("Abstract recovery: %s failed: %s", name, e)
            return False
        if text and len(text) < MIN_ABSTRACT_CHARS:
            # A few words from a structured source are an error string or a
            # tagline, not an abstract — the scrape path already applied this
            # floor internally; now every source does (N2 gate, F3).
            logger.info("Abstract recovery: %s returned only %d chars — ignored",
                        name, len(text))
            return False
        if text:
            result.text, result.source = text, name
            logger.info("Abstract recovery: found %d chars via %s", len(text), name)
            return True
        return False

    epmc = None
    if doi:
        epmc = _europepmc()
        raw_holder: Dict[str, Any] = {}

        def by_doi():
            raw = epmc.get_by_id(doi)
            raw_holder["raw"] = raw or {}
            return (raw or {}).get("abstractText", "")

        if attempt("europepmc", by_doi):
            return result
        pmcid = pmcid or (raw_holder.get("raw", {}).get("pmcid") or "")

    if pmcid:
        epmc = epmc or _europepmc()
        if attempt("pmc_fulltext", lambda: epmc.fetch_abstract_from_fulltext(pmcid)):
            return result

    if doi:
        if attempt("crossref", lambda: _crossref_abstract(doi)):
            return result
        if attempt("openalex", lambda: fetch_openalex_abstract(doi)):
            return result

    urls = []
    for key in ("best_oa_url", "source_url", "url"):
        url = (paper.get(key) or "").strip()
        if url and url not in urls:
            urls.append(url)
    if doi:
        urls.append(f"https://doi.org/{doi}")
    for url in urls:
        if attempt("scrape", lambda u=url: scrape_abstract_from_url(u)):
            return result

    if result.tried:
        logger.info("Abstract recovery: nothing found (tried %s)",
                    ", ".join(dict.fromkeys(result.tried)))
    return result
