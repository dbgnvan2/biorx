"""
bioRxiv / medRxiv adapter (feature-flagged, disabled by default).
Wraps the existing BioRxivAPI class as a SearchAdapter.
Enable via sources_config.yaml: biorxiv_medrxiv.enabled = true
"""

from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Sequence, Optional, Dict, Any, List
import logging

# Allow import when run standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .base import RawRecord, with_retry
from .schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
from .errors import SourceUnavailableError, RateLimitedError
from .config import polite_user_agent
from .query_builder import get_date_range

logger = logging.getLogger(__name__)

DEFAULT_SERVERS = ("biorxiv", "medrxiv")
SERVER_LABELS = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv"}
# The details endpoint's page size is not assumed: it was 100 and is 30 today
# (checked 2026-09-29). Each server's cursor advances by what it sent, and a
# server is done when its reported total is reached or it sends nothing.


class BiorxivMedrxivAdapter:
    """Search adapter wrapping the existing BioRxivAPI."""

    source_name = "biorxiv_medrxiv"
    source_trust_weight = 0.75

    def __init__(self, timeout: int = 30, sources_config: dict | None = None):
        self.timeout = timeout
        self.sources_config = sources_config or {}
        self.last_page_size = 0
        self.last_total = 0
        self._totals: Dict[str, int] = {}
        self._next: Dict[str, int] = {}        # server -> next cursor
        self._exhausted: set = set()
        self.has_more = True
        from src.biorxiv_api import BioRxivAPI
        self._api = BioRxivAPI(timeout=timeout, user_agent=polite_user_agent(self.sources_config))

    def for_search(self) -> "BiorxivMedrxivAdapter":
        """A new instance (and HTTP session) for one search (review B6)."""
        return type(self)(timeout=self.timeout, sources_config=self.sources_config)

    def _servers(self) -> List[str]:
        cfg = (self.sources_config.get("biorxiv_medrxiv") or {})
        servers = cfg.get("servers") or list(DEFAULT_SERVERS)
        return [s for s in servers if s in DEFAULT_SERVERS]

    def search(
        self, query: str, page: int = 1, page_size: int = 100,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> Sequence[RawRecord]:
        """
        Fetch one page of bioRxiv and medRxiv papers for the filter's date range.

        Purpose: Search every configured server over the filter's own dates.
        Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B7
        Tests:   tests/test_adapters.py::test_b7_date_range_used,
                 tests/test_adapters.py::test_b7_medrxiv_queried,
                 tests/test_adapters.py::test_b7_pages_through_each_server,
                 tests/test_adapters.py::test_b7_reads_past_a_30_paper_page

        The API is date-range based, not keyword-based; text filtering happens
        client-side. `query` is accepted but not sent. Each call returns one
        page per server, the union of the servers' pages. A server's cursor
        advances by the papers it sent; it is done when it has sent its
        reported total, or nothing. `has_more` tells the caller whether any
        server has more (the page size is the API's, not ours: assuming 100
        when it sent 30 read 60 of ~4,900 papers and said nothing — browser
        run 2026-09-29).
        """
        fd = filter_dict or {}
        start_date, end_date = get_date_range(fd)
        category = fd.get("category", "(any)")
        category = None if category in ("(any)", "", None) else category
        if page == 1:
            self._totals = {}
            self._next = {}
            self._exhausted = set()

        out: List[RawRecord] = []
        returned = 0
        for server in self._servers():
            if server in self._exhausted:
                continue
            cursor = self._next.get(server, 0)
            resp = with_retry(
                lambda: self._api.search_by_date_range(
                    start_date, end_date, category=category, server=server, cursor=cursor),
                source_label=SERVER_LABELS[server],
            )
            papers = self._api.parse_papers(resp)
            msgs = resp.get("messages", [{}]) or [{}]
            try:
                self._totals[server] = int(msgs[0].get("total", 0) or 0)
            except (TypeError, ValueError):
                self._totals[server] = 0
            self._next[server] = cursor + len(papers)
            if not papers or (self._totals[server]
                              and self._next[server] >= self._totals[server]):
                self._exhausted.add(server)
            for p in papers:
                if not p.get("server"):
                    p["server"] = server
            returned += len(papers)
            out.extend(papers)
        total = sum(self._totals.values())
        # Page-end detection uses what the servers sent, and the summed totals
        # as the source's total (the orchestrator reads both).
        self.last_page_size = returned
        self.last_total = total
        self.has_more = any(s not in self._exhausted for s in self._servers())
        return out

    def get_by_id(self, identifier: str) -> Optional[RawRecord]:
        """Fetch a single bioRxiv paper by DOI (not efficiently supported)."""
        return None

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize a bioRxiv parsed paper dict to CanonicalRecord."""
        doi     = raw.get("doi", "") or ""
        version = str(raw.get("version") or "1")

        # Authors: bioRxiv returns authors as a raw string
        authors: List[AuthorRecord] = []
        authors_raw = raw.get("authors", "") or ""
        if isinstance(authors_raw, list):
            for i, a in enumerate(authors_raw):
                authors.append(AuthorRecord(display_name=str(a), sequence=i + 1))
        elif isinstance(authors_raw, str) and authors_raw:
            # "Smith, J.; Doe, A.": surname before the comma.
            for i, name in enumerate(authors_raw.split(";")):
                name = name.strip()
                family = name.split(",")[0].strip() if "," in name else ""
                authors.append(AuthorRecord(display_name=name, sequence=i + 1, family=family))

        first_author = authors[0].display_name.split()[-1] if authors else ""

        pub_date = raw.get("pub_date") or raw.get("date", "")
        try:
            year = int(pub_date[:4]) if pub_date else 0
        except (ValueError, IndexError):
            year = 0

        server = raw.get("server") or "biorxiv"
        source_url = ""
        if doi:
            source_url = f"https://www.{server}.org/content/{doi}v{version}"

        cid = make_canonical_id(doi=doi, title=raw.get("title", ""),
                                first_author=first_author, year=year)

        return CanonicalRecord(
            canonical_id=cid,
            title=raw.get("title", "") or "",
            abstract=raw.get("abstract", "") or "",
            authors=authors,
            year=year,
            published_date=pub_date,
            document_type="preprint",
            is_preprint=True,
            journal_or_server=server,
            doi=doi,
            pmid="",
            pmcid="",
            source_url=source_url,
            best_oa_url=f"{source_url}.full.pdf" if source_url else "",
            pdf_url=f"{source_url}.full.pdf" if source_url else "",
            license=raw.get("license", "") or "",
            oa_status="open",
            subjects=[raw.get("category", "")] if raw.get("category") else [],
            keywords=[],
            source_hits=[SourceHit(
                source=self.source_name,
                source_record_id=doi,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )],
            flags=RecordFlags(fulltext_reusable=True),  # bioRxiv is freely accessible
            source_trust_weight=self.source_trust_weight,
            version=version,        # review M18: the revised-only filter reads it
        )
