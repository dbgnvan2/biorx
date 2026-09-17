"""
SearchAdapter Protocol — all search-capable source adapters must satisfy this interface.
Enrichment-only adapters (Crossref, Unpaywall) implement a subset.
"""

import logging
import time
from typing import Protocol, Sequence, Optional, Dict, Any

import requests

from .schema import CanonicalRecord
from .errors import SourceUnavailableError

_logger = logging.getLogger(__name__)


def with_retry(fn, *, max_attempts: int = 3, source_label: str = ""):
    """Retry fn() on transient network failures (RequestException, HTTP 5xx).

    Returns whatever fn() returns on success. After max_attempts exhausted,
    raises SourceUnavailableError. 429 and other 4xx are returned/raised to
    the caller unchanged — they are not retried.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code < 500:
                raise  # 4xx — permanent; let caller handle 404/429/etc.
            if attempt >= max_attempts:
                status = exc.response.status_code if exc.response is not None else "?"
                raise SourceUnavailableError(
                    f"{source_label} returned {status} after {max_attempts} attempts"
                ) from exc
            backoff = 2 ** (attempt - 1)
            _logger.warning(
                "%s HTTP error; retrying in %ds (attempt %d/%d): %s",
                source_label, backoff, attempt, max_attempts, exc,
            )
            time.sleep(backoff)
            continue
        except requests.RequestException as exc:
            if attempt >= max_attempts:
                raise SourceUnavailableError(
                    f"{source_label} unreachable: {exc}"
                ) from exc
            backoff = 2 ** (attempt - 1)
            _logger.warning(
                "%s request failed; retrying in %ds (attempt %d/%d): %s",
                source_label, backoff, attempt, max_attempts, exc,
            )
            time.sleep(backoff)
            continue
        # Retry bare 5xx responses (not raised as HTTPError)
        if isinstance(result, requests.Response) and result.status_code >= 500:
            if attempt >= max_attempts:
                raise SourceUnavailableError(
                    f"{source_label} returned {result.status_code} after {max_attempts} attempts"
                )
            backoff = 2 ** (attempt - 1)
            _logger.warning(
                "%s returned %d; retrying in %ds (attempt %d/%d)",
                source_label, result.status_code, backoff, attempt, max_attempts,
            )
            time.sleep(backoff)
            continue
        return result

    raise SourceUnavailableError(f"{source_label} failed after {max_attempts} attempts")

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
