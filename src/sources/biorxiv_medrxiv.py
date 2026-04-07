"""
bioRxiv / medRxiv adapter (feature-flagged, disabled by default).
Wraps the existing BioRxivAPI class as a SearchAdapter.
Enable via sources_config.yaml: biorxiv_medrxiv.enabled = true
"""

from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime
from typing import Sequence, Optional, Dict, Any, List
import logging

# Allow import when run standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .base import RawRecord
from .schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
from .errors import SourceUnavailableError, RateLimitedError

logger = logging.getLogger(__name__)


class BiorxivMedrxivAdapter:
    """Search adapter wrapping the existing BioRxivAPI."""

    source_name = "biorxiv_medrxiv"
    source_trust_weight = 0.75

    def __init__(self, timeout: int = 30):
        from src.biorxiv_api import BioRxivAPI
        self._api = BioRxivAPI(timeout=timeout)

    def search(
        self, query: str, page: int = 1, page_size: int = 100,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> Sequence[RawRecord]:
        """
        Fetch bioRxiv papers for the date range in filter_dict.

        The bioRxiv API is date-range based, not keyword-based.
        Text filtering happens client-side via _filter_papers().
        `query` is accepted but ignored for the API call.

        Args:
            query:       Ignored (bioRxiv uses date-range, not keywords).
            page:        Page number (maps to cursor = (page-1) * page_size).
            page_size:   Papers per page (bioRxiv returns 100/page).
            filter_dict: Filter dict with days_back / category.

        Returns:
            List of raw bioRxiv paper dicts (already parsed by BioRxivAPI.parse_papers).
        """
        import requests

        fd = filter_dict or {}
        days_back = fd.get("days_back", 7)
        category  = fd.get("category", "(any)")
        category  = None if category == "(any)" else category
        cursor    = (page - 1) * 100

        try:
            resp   = self._api.search_recent(days=days_back, category=category,
                                             server="biorxiv", cursor=cursor)
            papers = self._api.parse_papers(resp)
            # Attach total to each paper dict for progress reporting
            msgs  = resp.get("messages", [{}])
            total = int(msgs[0].get("total", 0)) if msgs else 0
            for p in papers:
                p["_total"] = total
            return papers
        except requests.RequestException as e:
            raise SourceUnavailableError(f"bioRxiv unreachable: {e}") from e

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
            for i, name in enumerate(authors_raw.split(";")):
                authors.append(AuthorRecord(display_name=name.strip(), sequence=i + 1))

        first_author = authors[0].display_name.split()[-1] if authors else ""

        pub_date = raw.get("pub_date") or raw.get("date", "")
        try:
            year = int(pub_date[:4]) if pub_date else 0
        except (ValueError, IndexError):
            year = 0

        server = raw.get("server", "biorxiv")
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
                fetched_at=datetime.utcnow().isoformat(),
            )],
            flags=RecordFlags(fulltext_reusable=True),  # bioRxiv is freely accessible
            source_trust_weight=self.source_trust_weight,
        )
