"""
arXiv adapter implementing the SearchAdapter protocol.
Queries arXiv API for CS/LLM/agent-simulation literature.
"""

from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime
from typing import Sequence, Optional, Dict, Any, List
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from .config import polite_user_agent

# Allow import when run standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .base import RawRecord
from .schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags
from .errors import SourceUnavailableError, RateLimitedError

logger = logging.getLogger(__name__)

# arXiv API namespaces
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
OPENSEARCH_NS = {"os": "http://a9.com/-/spec/opensearch/1.1/"}

# HEURISTIC, not a documented arXiv field: arXiv has no machine-readable
# "withdrawn" flag in the Atom feed, so withdrawal is detected from the
# conventional notice authors leave in the abstract ("This paper has been
# withdrawn by the author"). Anchored to that sentence form and to the start of
# the abstract, because a bare "withdrawn" substring also matches ordinary
# research prose — "participants withdrawn from the study", "the drug was
# withdrawn" — and would silently delete real papers from the results.
# Drops are counted and logged (see last_dropped_withdrawn) so the heuristic is
# auditable rather than invisible.
_WITHDRAWN_RE = re.compile(
    r"(?:this|the)\s+(?:paper|submission|manuscript|article|preprint|work|entry|version|result)\s+"
    r"(?:has\s+been|have\s+been|was|is|been)?\s*withdrawn\b"
    r"|\bwithdrawn\s+by\s+the\s+author",
    re.IGNORECASE,
)

# The notice appears at the top of the abstract; a match further in is prose.
_WITHDRAWN_SEARCH_WINDOW = 200


def _is_withdrawn(summary: str) -> bool:
    """True when an abstract opens with arXiv's conventional withdrawal notice."""
    if not summary:
        return False
    m = _WITHDRAWN_RE.search(summary)
    return m is not None and m.start() < _WITHDRAWN_SEARCH_WINDOW


class ArxivAdapter:
    """Search adapter for arXiv.org preprint repository."""

    source_name = "arxiv"
    source_trust_weight = 0.75

    def __init__(self, timeout: int = 30, min_request_interval: float = 3.0,
                 sources_config: dict | None = None):
        self.timeout = timeout
        self.min_request_interval = min_request_interval
        self.sources_config = sources_config or {}
        self.last_request_time = 0.0
        self.last_total = 0
        # Entries present in the last page BEFORE withdrawn ones were removed.
        # The orchestrator uses page size to detect the last page, so it must see
        # what arXiv returned, not what survived filtering (otherwise one
        # withdrawn paper on a full page ends pagination early).
        self.last_page_size = 0
        self.last_dropped_withdrawn = 0

    def search(
        self, query: str, page: int = 1, page_size: int = 50,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> Sequence[RawRecord]:
        """
        Search arXiv via the public API.

        Args:
            query:       arXiv query string (e.g. "cat:cs.AI AND ti:agent")
            page:        1-based page number
            page_size:   Results per page (max ~2000)
            filter_dict: Ignored; query is pre-built

        Returns:
            List of raw arXiv entry dicts

        Raises:
            SourceUnavailableError: API unreachable or 5xx
            RateLimitedError: 429 with Retry-After
        """
        # Enforce minimum request interval
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)

        url = "https://export.arxiv.org/api/query"
        start = (page - 1) * page_size
        params = {
            "search_query": query,
            "start": start,
            "max_results": page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        headers = {
            "User-Agent": polite_user_agent(self.sources_config),
        }

        attempts = 0
        max_attempts = 3

        while attempts < max_attempts:
            attempts += 1
            try:
                self.last_request_time = time.time()
                resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)

                # Handle 429 (rate limited)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    if attempts >= max_attempts:
                        raise RateLimitedError(f"arXiv rate limited; gave up after {max_attempts} attempts")
                    logger.warning("arXiv rate limited; sleeping %d seconds", retry_after)
                    time.sleep(retry_after)
                    continue

                # Handle 5xx — retry with exponential backoff (M1: transient, not terminal)
                if resp.status_code >= 500:
                    if attempts >= max_attempts:
                        raise SourceUnavailableError(
                            f"arXiv returned {resp.status_code} after {max_attempts} attempts"
                        )
                    backoff = 2 ** (attempts - 1)
                    logger.warning(
                        "arXiv returned %d; retrying in %ds (attempt %d/%d)",
                        resp.status_code, backoff, attempts, max_attempts,
                    )
                    time.sleep(backoff)
                    continue

                # Handle other errors
                resp.raise_for_status()

                # Parse Atom XML response
                try:
                    root = ET.fromstring(resp.content)
                except ET.ParseError as e:
                    raise SourceUnavailableError(f"Failed to parse arXiv response: {e}") from e

                # Extract total results
                total_elem = root.find("os:totalResults", OPENSEARCH_NS)
                self.last_total = int(total_elem.text) if total_elem is not None else 0

                # Extract entries
                entry_elems = root.findall("atom:entry", ATOM_NS)
                self.last_page_size = len(entry_elems)
                self.last_dropped_withdrawn = 0

                entries = []
                for entry_elem in entry_elems:
                    raw = self._parse_entry(entry_elem)
                    if raw is None:      # withdrawn — dropped, but counted below
                        self.last_dropped_withdrawn += 1
                        continue
                    entries.append(raw)

                if self.last_dropped_withdrawn:
                    logger.info(
                        "arXiv page %d: dropped %d of %d entries as withdrawn",
                        page, self.last_dropped_withdrawn, self.last_page_size,
                    )

                return entries

            except requests.RequestException as e:
                if attempts >= max_attempts:
                    raise SourceUnavailableError(f"arXiv request failed: {e}") from e
                backoff = 2 ** (attempts - 1)
                logger.warning(
                    "arXiv request failed: %s; retrying in %ds (attempt %d/%d)",
                    e, backoff, attempts, max_attempts,
                )
                time.sleep(backoff)

        raise SourceUnavailableError("arXiv request exhausted retries")

    def _parse_entry(self, entry: ET.Element) -> Optional[RawRecord]:
        """
        Parse a single Atom entry into a raw dict.
        Returns None if entry is withdrawn.
        """
        # Check for withdrawn status (see _WITHDRAWN_RE — heuristic, anchored)
        summary_elem = entry.find("atom:summary", ATOM_NS)
        if summary_elem is not None and _is_withdrawn(summary_elem.text or ""):
            return None

        # Extract fields
        title_elem = entry.find("atom:title", ATOM_NS)
        title = title_elem.text.strip() if title_elem is not None else ""

        # arXiv ID (full with version)
        id_elem = entry.find("atom:id", ATOM_NS)
        arxiv_id_full = ""
        if id_elem is not None and id_elem.text:
            # ID format: http://arxiv.org/abs/2301.12345v2 or http://arxiv.org/abs/2301.12345
            arxiv_id_full = id_elem.text.split("/abs/")[-1] if "/abs/" in id_elem.text else id_elem.text

        published_elem = entry.find("atom:published", ATOM_NS)
        published = published_elem.text[:10] if published_elem is not None else ""  # YYYY-MM-DD

        # Authors (list of name strings)
        authors = []
        for author_elem in entry.findall("atom:author", ATOM_NS):
            name_elem = author_elem.find("atom:name", ATOM_NS)
            if name_elem is not None and name_elem.text:
                authors.append(name_elem.text)

        # Categories (arXiv subject classifications)
        categories = []
        for cat_elem in entry.findall("atom:category", ATOM_NS):
            term = cat_elem.get("term", "")
            if term:
                categories.append(term)

        abstract = summary_elem.text.strip() if summary_elem is not None else ""

        return {
            "arxiv_id_full": arxiv_id_full,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "published": published,
            "categories": categories,
        }

    def get_by_id(self, identifier: str) -> Optional[RawRecord]:
        """Not implemented; return None."""
        return None

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize a raw arXiv entry to CanonicalRecord."""
        arxiv_id_full = raw.get("arxiv_id_full", "")
        # Strip vN suffix for canonical identity; carry version number separately
        # so the "2+ (revised only)" version filter can match arXiv papers.
        arxiv_id_no_version = re.sub(r"v\d+$", "", arxiv_id_full)
        version_match = re.search(r"v(\d+)$", arxiv_id_full)
        arxiv_version = version_match.group(1) if version_match else ""

        # Authors
        authors: List[AuthorRecord] = []
        for i, name in enumerate(raw.get("authors", []), start=1):
            authors.append(AuthorRecord(display_name=name, sequence=i))

        # Published date and year
        published = raw.get("published", "")
        try:
            year = int(published[:4]) if published else 0
        except (ValueError, IndexError):
            year = 0

        return CanonicalRecord(
            canonical_id=f"arxiv:{arxiv_id_no_version}",
            title=raw.get("title", "") or "",
            abstract=raw.get("abstract", "") or "",
            authors=authors,
            year=year,
            published_date=published,
            document_type="preprint",
            is_preprint=True,
            journal_or_server="arXiv",
            doi="",
            pmid="",
            pmcid="",
            source_url=f"https://arxiv.org/abs/{arxiv_id_full}",
            best_oa_url=f"https://arxiv.org/pdf/{arxiv_id_full}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id_full}",
            license="",
            oa_status="open",
            subjects=raw.get("categories", []),
            keywords=[],
            source_hits=[SourceHit(
                source=self.source_name,
                source_record_id=arxiv_id_full,
                fetched_at=datetime.utcnow().isoformat(),
            )],
            flags=RecordFlags(fulltext_reusable=True),
            source_trust_weight=self.source_trust_weight,
            arxiv_version=arxiv_version,
        )
