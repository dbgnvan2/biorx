"""
Unpaywall enrichment adapter.
Resolves best legal open-access URL after a DOI is known.
NOT a search source; never appears in the source picker.
API docs: https://unpaywall.org/products/api
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import logging
import requests

from .schema import CanonicalRecord
from .errors import SourceUnavailableError, RateLimitedError

logger = logging.getLogger(__name__)

BASE_URL = "https://api.unpaywall.org/v2"


class UnpaywallAdapter:
    """Unpaywall OA lookup adapter (enrichment only)."""

    source_name = "unpaywall"

    def __init__(self, email: str = "research@example.com", timeout: int = 15):
        self.email   = email
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ResearchTool/1.0"})

    def get_by_id(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        Lookup OA information for a DOI.

        Args:
            doi: DOI string.

        Returns:
            Unpaywall response dict or None if not found.
        """
        import re
        clean = re.sub(r"^https?://doi\.org/", "", doi.strip())
        try:
            resp = self.session.get(
                f"{BASE_URL}/{clean}",
                params={"email": self.email},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise SourceUnavailableError(f"Unpaywall unreachable: {e}") from e

        if resp.status_code in (404, 422):
            # 404 = DOI not found; 422 = DOI not indexed by Unpaywall — both are normal
            return None
        if resp.status_code == 429:
            raise RateLimitedError("Unpaywall rate limit hit")
        if not resp.ok:
            raise SourceUnavailableError(f"Unpaywall returned {resp.status_code}")

        return resp.json()

    def enrich(self, record: CanonicalRecord) -> None:
        """
        Enrich a CanonicalRecord in-place with OA location data.
        No-op if the record has no DOI or Unpaywall lookup fails.
        """
        if not record.doi:
            return
        try:
            data = self.get_by_id(record.doi)
        except (SourceUnavailableError, RateLimitedError) as e:
            logger.warning("Unpaywall enrich failed for %s: %s", record.doi, e)
            return
        if not data:
            return

        oa_status = data.get("oa_status", "")
        record.oa_status = oa_status

        best_loc = data.get("best_oa_location") or {}
        url_for_pdf = best_loc.get("url_for_pdf", "") or ""
        url_for_landing = best_loc.get("url", "") or ""

        if url_for_pdf and not record.best_oa_url:
            record.best_oa_url = url_for_pdf
            record.pdf_url     = url_for_pdf
        elif url_for_landing and not record.best_oa_url:
            record.best_oa_url = url_for_landing

        license_ = best_loc.get("license", "") or ""
        if license_ and not record.license:
            record.license = license_

        # Mark fulltext reusable only if OA and has a CC license
        is_oa = oa_status in ("gold", "green", "hybrid", "bronze")
        has_reusable_license = any(
            kw in (license_ or record.license).lower()
            for kw in ("cc-by", "cc by", "public domain", "cc0")
        )
        if is_oa and has_reusable_license and record.best_oa_url:
            record.flags.fulltext_reusable = True

        logger.debug("Unpaywall enriched: %s oa_status=%s", record.doi, oa_status)
