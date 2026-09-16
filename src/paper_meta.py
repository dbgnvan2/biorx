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
import re
from html.parser import HTMLParser
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

# Named rather than inline so they are tunable and greppable (E1, P4).
SCRAPE_TIMEOUT = 20
OPENALEX_TIMEOUT = 15
MIN_ABSTRACT_CHARS = 80          # below this, a "description" is a site tagline
MIN_HTML_CHARS = 500             # below this, the response is an error/interstitial
OPENALEX_USER_AGENT = "ResearchTool/1.0 (mailto:davegalloway@me.com)"

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
            headers={"User-Agent": OPENALEX_USER_AGENT},
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
