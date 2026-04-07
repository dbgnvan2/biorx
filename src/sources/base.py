"""
SearchAdapter Protocol — all search-capable source adapters must satisfy this interface.
Enrichment-only adapters (Crossref, Unpaywall) implement a subset.
"""

from typing import Protocol, Sequence, Optional, Dict, Any
from .schema import CanonicalRecord

# Raw API response — source-specific dict, before normalization
RawRecord = Dict[str, Any]


class SearchAdapter(Protocol):
    """Interface for search-capable source adapters."""

    source_name: str
    source_trust_weight: float

    def search(
        self, query: str, page: int = 1, page_size: int = 25
    ) -> Sequence[RawRecord]:
        """
        Search the source.

        Args:
            query: Query string (Lucene for Europe PMC; plain text for others).
            page:  1-based page number.
            page_size: Results per page.

        Returns:
            Sequence of raw response dicts.

        Raises:
            SourceUnavailableError: Source is unreachable.
            RateLimitedError: Source returned 429.
        """
        ...

    def get_by_id(self, identifier: str) -> Optional[RawRecord]:
        """
        Fetch a single record by its native identifier (DOI, PMID, etc.).

        Returns:
            Raw record dict, or None if not found.
        """
        ...

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize a raw API record to CanonicalRecord."""
        ...
