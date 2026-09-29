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
import html as html_lib
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

def scrape_abstract_from_url(url: str, fetch_html=None) -> str:
    """
    Attempt to scrape an abstract from a publisher page.

    fetch_html, when given, fetches the page instead of plain requests. The web
    app passes src.safe_fetch.fetch_html, because there the URL can come from a
    client (POST /api/summaries); the desktop app, run by its owner, does not.

    Tries in order:
      1. JSON-LD structured data (schema.org/ScholarlyArticle description)
      2. <meta name="description"> / og:description
      3. Common HTML patterns: section/div with id or class containing "abstract"
    Returns empty string if nothing useful is found or the page is paywalled.
    """
    try:
        if fetch_html is not None:
            html = fetch_html(url, headers=_BROWSER_HEADERS, timeout=SCRAPE_TIMEOUT)
        else:
            resp = requests.get(
                url, headers=_BROWSER_HEADERS, timeout=SCRAPE_TIMEOUT, allow_redirects=True
            )
            if not resp.ok:
                return ""
            html = resp.text
        if len(html) < MIN_HTML_CHARS:
            return ""
    except Exception as e:
        logger.debug("Abstract scrape fetch failed for %s: %s", url, e)
        return ""

    return _abstract_from_html(html)


# The scraper reads at most this much of a page. Abstracts sit near the top;
# the cap bounds the work a hostile page can cause (review B8).
SCRAPE_MAX_CHARS = 512_000
# A tag longer than this is treated as text: real tags are short, and every
# attribute regex below runs on one tag at a time, never on the page.
_MAX_TAG_CHARS = 4000

_ABSTRACT_META = ("description", "og:description", "citation_abstract")
_TAG_NAME = re.compile(r"^/?\s*([a-zA-Z][a-zA-Z0-9:-]*)")
_ATTR = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""")


def _scan_html(page: str):
    """Yield ("start", name, attrs) / ("end", name) / ("data", text) for a page.

    Purpose: Walk untrusted HTML in time proportional to its length.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B8
    Tests:   tests/test_paper_meta.py::test_b8_adversarial_html_is_linear

    Neither regular expressions over the page (they backtracked: review B8)
    nor html.parser (quadratic on unclosed tags in Python before 3.12.11,
    measured here at 67 s for 320 KB) bound the work on hostile input. This
    indexes every "<" and ">" once; a tag is the text from a "<" to the next
    ">", unless another "<" comes first, in which case the "<" is text.
    """
    import bisect
    lowered = page.lower()       # once: per-script .lower() would be quadratic
    lts = [m.start() for m in re.finditer("<", page)]
    gts = [m.start() for m in re.finditer(">", page)]
    i = 0
    n = len(page)
    li = 0
    while i < n:
        li = bisect.bisect_left(lts, i, li)
        if li >= len(lts):
            yield ("data", page[i:])
            return
        lt = lts[li]
        if lt > i:
            yield ("data", page[i:lt])
        gi = bisect.bisect_left(gts, lt)
        gt = gts[gi] if gi < len(gts) else -1
        nxt = lts[li + 1] if li + 1 < len(lts) else n
        if gt == -1 or gt > nxt or gt - lt > _MAX_TAG_CHARS:
            yield ("data", page[lt:nxt])        # a stray "<": text
            i = nxt
            continue
        tag = page[lt + 1:gt]
        i = gt + 1
        if tag.startswith("!") or tag.startswith("?"):
            continue                            # comment, doctype
        m = _TAG_NAME.match(tag)
        if not m:
            yield ("data", page[lt:gt + 1])
            continue
        name = m.group(1).lower()
        if tag.lstrip().startswith("/"):
            yield ("end", name)
            continue
        attrs = {}
        for am in _ATTR.finditer(tag, m.end()):
            value = am.group(2) or ""
            if value[:1] in ("'", '"'):
                value = value[1:-1]
            attrs[am.group(1).lower()] = html_lib.unescape(value)
        yield ("start", name, attrs)
        if name == "script" and not tag.rstrip().endswith("/"):
            end = lowered.find("</script", i)
            if end == -1:
                yield ("data", page[i:])
                return
            yield ("data", page[i:end])
            i = end


def _abstract_from_html(page: str) -> str:
    """The abstract a publisher page carries, or "". Tries, in order:
    JSON-LD (schema.org description/abstract), the description meta tags, and
    an element whose id or class names it the abstract (review B8: one linear
    pass, see _scan_html)."""
    page = page[:SCRAPE_MAX_CHARS]
    json_ld = []
    meta = {}
    in_ld = False
    abs_tag, abs_depth, abs_done = None, 0, False
    parts = []
    for item in _scan_html(page):
        kind = item[0]
        if kind == "start":
            _, name, attrs = item
            if name == "script":
                in_ld = attrs.get("type", "").lower() == "application/ld+json"
                continue
            if name == "meta":
                key = (attrs.get("name") or attrs.get("property") or "").lower()
                if key in _ABSTRACT_META and key not in meta:
                    meta[key] = attrs.get("content", "")
            if abs_tag is not None:
                if name == abs_tag:
                    abs_depth += 1
                continue
            if abs_done or name not in ("section", "div", "p"):
                continue
            ident = f"{attrs.get('id', '')} {attrs.get('class', '')}".lower()
            if re.search(r"\babstract\b", ident) or attrs.get("id", "").lower().startswith("abs1"):
                abs_tag, abs_depth = name, 1
        elif kind == "end":
            name = item[1]
            if name == "script":
                in_ld = False
            elif abs_tag is not None and name == abs_tag:
                abs_depth -= 1
                if abs_depth == 0:
                    abs_tag, abs_done = None, True
        else:
            if in_ld:
                json_ld.append(item[1])
            elif abs_tag is not None:
                parts.append(item[1])

    for blob in json_ld:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            # Springer/BMC wrap the article in WebPage > mainEntity.
            for candidate in (obj, obj.get("mainEntity") or {}):
                if not isinstance(candidate, dict):
                    continue
                desc = candidate.get("description") or candidate.get("abstract") or ""
                if isinstance(desc, str) and len(desc) > MIN_ABSTRACT_CHARS:
                    return _strip_tags(desc)

    for key in _ABSTRACT_META:
        text = meta.get(key, "").strip()
        if len(text) > MIN_ABSTRACT_CHARS:
            return _strip_tags(text)

    text = re.sub(r"\s+", " ", html_lib.unescape(" ".join(parts))).strip()
    return text if len(text) > MIN_ABSTRACT_CHARS else ""


def _strip_tags(text: str) -> str:
    """Remove markup from a description string (linear, as _scan_html)."""
    return html_lib.unescape("".join(i[1] for i in _scan_html(text) if i[0] == "data")).strip()


def fetch_openalex_abstract(doi: str) -> str:
    """
    Fetch abstract from OpenAlex via its inverted-index format.
    OpenAlex stores abstracts as {word: [position, ...]} dicts to work around
    publisher restrictions; we reconstruct the plain text here.

    Purpose: Recover an abstract from OpenAlex, telling "none" from "unreachable".
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M21
    Tests:   tests/test_paper_meta.py::test_m21_openalex_retried,
             tests/test_paper_meta.py::test_m21_outage_reported_as_failed

    Returns "" when OpenAlex has no such work or no abstract. Raises
    SourceUnavailableError (after retries) or RateLimitedError when OpenAlex
    could not answer, so the caller reports it as a failure to retry (P1).
    """
    from src.sources.base import with_retry
    from src.sources.errors import RateLimitedError, SourceUnavailableError

    clean = re.sub(r"^https?://doi\.org/", "", doi.strip())
    resp = with_retry(
        lambda: requests.get(
            f"https://api.openalex.org/works/doi:{clean}",
            params={"select": "abstract_inverted_index"},
            headers={"User-Agent": openalex_user_agent()},
            timeout=OPENALEX_TIMEOUT,
        ),
        source_label="OpenAlex",
    )
    if resp.status_code == 404:
        return ""
    if resp.status_code == 429:
        raise RateLimitedError("OpenAlex rate limit hit")
    if not resp.ok:
        raise SourceUnavailableError(f"OpenAlex returned {resp.status_code}")
    try:
        idx = resp.json().get("abstract_inverted_index") or {}
    except ValueError as exc:
        raise SourceUnavailableError(f"OpenAlex sent an unreadable reply: {exc}") from exc
    if not idx:
        return ""
    # Reconstruct: sort (position, word) pairs and join
    pairs = sorted(
        (pos, word)
        for word, positions in idx.items()
        for pos in positions
    )
    return " ".join(word for _, word in pairs)


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
    # Sources that could not be asked (outage, timeout) — distinct from ones
    # that were asked and had nothing (review finding 5, 2026-09-18).
    failed: List[str] = field(default_factory=list)

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
    from .sources.config import load_sources_config
    try:
        cfg = load_sources_config()
    except Exception:
        cfg = {}
    return EuropePmcAdapter(sources_config=cfg)


def _crossref_abstract(doi: str) -> str:
    """Crossref sometimes carries a JATS abstract that Europe PMC lacks."""
    from .sources.crossref import CrossrefAdapter
    from .sources.config import get_crossref_user_agent, load_sources_config
    from .sources.schema import CanonicalRecord, RecordFlags, make_canonical_id

    try:
        cfg = load_sources_config()
    except Exception:
        cfg = {}
    record = CanonicalRecord(
        canonical_id=make_canonical_id(doi=doi, title="", first_author="", year=0),
        title="", abstract="", authors=[], year=0, published_date="",
        document_type="article", is_preprint=False, journal_or_server="", doi=doi,
        pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="", license="",
        oa_status="", subjects=[], keywords=[], source_hits=[], flags=RecordFlags(),
    )
    if CrossrefAdapter(user_agent=get_crossref_user_agent(cfg)).enrich(record) is False:
        # enrich() reports an outage by returning False; raise so attempt()
        # records "could not reach" rather than "had nothing".
        raise RuntimeError("Crossref could not be reached")
    return record.abstract or ""


def recover_abstract(paper: Dict[str, Any], fetch_html=None) -> AbstractRecovery:
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
            result.failed.append(name)
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
        scrape = ((lambda u=url: scrape_abstract_from_url(u, fetch_html=fetch_html))
                  if fetch_html is not None else (lambda u=url: scrape_abstract_from_url(u)))
        if attempt("scrape", scrape):
            return result

    if result.tried:
        logger.info("Abstract recovery: nothing found (tried %s)",
                    ", ".join(dict.fromkeys(result.tried)))
    return result
