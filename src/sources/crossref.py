"""
Crossref enrichment adapter.
Enrichment-only — fills missing metadata (DOI resolution, publisher, funder, license).
NOT a search source; never appears in the source picker.
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import logging
import re
import requests

from .base import RawRecord, with_retry
from .schema import CanonicalRecord
from .errors import SourceUnavailableError, RateLimitedError

logger = logging.getLogger(__name__)

BASE_URL = "https://api.crossref.org/works"


def _strip_jats(text: str) -> str:
    """Strip JATS/XML markup from a Crossref abstract string.

    Crossref returns abstracts like:
        <jats:p>Background: ...</jats:p><jats:p>Methods: ...</jats:p>
    We remove all tags and normalise whitespace to plain text.
    """
    if not text or "<" not in text:
        return text
    # Replace block-level tags with newlines to preserve paragraph breaks
    text = re.sub(r"</?(jats:p|jats:sec|jats:title|p|br)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse multiple blank lines, normalise whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


class CrossrefAdapter:
    """Crossref metadata enrichment adapter (DOI lookup only)."""

    source_name = "crossref"

    def __init__(self, user_agent: str = "biorx/1.0", timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def get_by_id(self, doi: str) -> Optional[RawRecord]:
        """
        Fetch Crossref metadata for a DOI.

        Args:
            doi: A DOI string (with or without https://doi.org/ prefix).

        Returns:
            Crossref 'message' dict or None if not found.
        """
        clean = re.sub(r"^https?://doi\.org/", "", doi.strip())
        resp = with_retry(
            lambda: self.session.get(f"{BASE_URL}/{clean}", timeout=self.timeout),
            source_label="Crossref",
        )
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            raise RateLimitedError("Crossref rate limit hit")
        if not resp.ok:
            raise SourceUnavailableError(f"Crossref returned {resp.status_code}")

        return resp.json().get("message")

    def enrich(self, record: CanonicalRecord) -> bool:
        """
        Returns False only when the lookup failed (unreachable or rate-limited),
        so the caller can count an outage; True otherwise, including "not found".

        Enrich a CanonicalRecord in-place using Crossref metadata.
        Only fills fields that are missing in the canonical record.
        No-op if the record has no DOI or Crossref lookup fails.
        """
        if not record.doi:
            return True
        try:
            msg = self.get_by_id(record.doi)
        except (SourceUnavailableError, RateLimitedError) as e:
            logger.warning("Crossref enrich failed for %s: %s", record.doi, e)
            return False
        if not msg:
            return True

        self.fill_from_message(record, msg)
        logger.debug("Crossref enriched: %s", record.doi)
        return True

    def fill_from_message(self, record: CanonicalRecord, msg: Dict[str, Any]) -> None:
        """Fill the record's empty fields from a Crossref 'message' dict."""
        # Fill missing title
        if not record.title:
            titles = msg.get("title", [])
            record.title = titles[0] if titles else ""

        # Fill missing abstract — strip JATS XML markup Crossref embeds
        if not record.abstract:
            record.abstract = _strip_jats(msg.get("abstract", "") or "")

        # Fill missing license
        if not record.license:
            licenses = msg.get("license", [])
            if licenses:
                record.license = licenses[0].get("URL", "")

        # Fill missing published_date
        if not record.published_date:
            dp = msg.get("published", {}).get("date-parts", [[]])
            if dp and dp[0]:
                parts = dp[0]
                record.published_date = "-".join(str(p).zfill(2) for p in parts)
                if not record.year and parts:
                    try:
                        record.year = int(parts[0])
                    except (ValueError, IndexError):
                        pass

        # Fill missing journal_or_server
        if not record.journal_or_server:
            # Often an empty list for posted content (review M19).
            record.journal_or_server = (msg.get("container-title") or [""])[0] or ""

        # Fill missing authors from Crossref
        if not record.authors:
            from .schema import AuthorRecord
            cr_authors = msg.get("author", [])
            for i, a in enumerate(cr_authors):
                name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                if name:
                    record.authors.append(AuthorRecord(
                        display_name=name,
                        orcid=a.get("ORCID", "").replace("http://orcid.org/", ""),
                        sequence=i + 1,
                        family=a.get("family", "") or "",
                    ))

    def record_for_doi(self, doi: str) -> Optional[CanonicalRecord]:
        """A record built from Crossref alone, or None if Crossref has no such DOI.

        Purpose: Look a paper up server-side by DOI (review A1).
        Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A1
        Tests:   tests/test_summarize.py::test_a1_doi_resolved_via_crossref

        Raises SourceUnavailableError / RateLimitedError when Crossref could not
        answer.
        """
        from .schema import RecordFlags, SourceHit, make_canonical_id
        from datetime import datetime, timezone
        msg = self.get_by_id(doi)
        if not msg:
            return None
        record = CanonicalRecord(
            canonical_id=make_canonical_id(doi=doi), title="", abstract="", authors=[],
            year=0, published_date="", document_type="other",
            is_preprint=(msg.get("type") == "posted-content"),
            journal_or_server="", doi=doi, pmid="", pmcid="",
            source_url=msg.get("URL") or f"https://doi.org/{doi}", best_oa_url="",
            pdf_url="", license="", oa_status="", subjects=[], keywords=[],
            source_hits=[SourceHit(source="crossref", source_record_id=doi,
                                   fetched_at=datetime.now(timezone.utc).isoformat())],
            flags=RecordFlags(), source_trust_weight=0.85)
        self.fill_from_message(record, msg)
        return record if record.title else None
