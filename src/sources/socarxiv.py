"""
SocArXiv (OSF) search adapter.
Social science preprints.
API docs: https://developer.osf.io/
"""

from __future__ import annotations
from datetime import datetime
from typing import Sequence, Optional, Dict, Any, List
import logging
import requests

from .base import RawRecord
from .schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
from .errors import SourceUnavailableError, RateLimitedError
from .query_builder import get_date_range

logger = logging.getLogger(__name__)

BASE_URL = "https://api.osf.io/v2/preprints/"


class SocArxivAdapter:
    """Search adapter for SocArXiv preprints via the OSF API."""

    source_name = "socarxiv"
    source_trust_weight = 0.75

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ResearchTool/1.0",
            "Accept":     "application/vnd.api+json",
        })

    def search(
        self, query: str, page: int = 1, page_size: int = 25,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> Sequence[RawRecord]:
        """Fetch recent SocArXiv preprints."""
        start_date, end_date = get_date_range(filter_dict or {})

        params: Dict[str, Any] = {
            "filter[provider]":                "socarxiv",
            "filter[date_created][gte]":       start_date,
            "filter[date_created][lte]":       end_date,
            "page[size]":                      min(page_size, 100),
            "page":                            page,
            "embed":                           "contributors",
        }

        if query:
            first_term = query.split()[0].rstrip("*") if query else ""
            if first_term:
                params["filter[title]"] = first_term

        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise SourceUnavailableError(f"SocArXiv (OSF) unreachable: {e}") from e

        if resp.status_code == 429:
            raise RateLimitedError("SocArXiv rate limit hit")
        if not resp.ok:
            raise SourceUnavailableError(f"SocArXiv returned {resp.status_code}")

        data = resp.json()
        results = data.get("data", [])
        logger.debug(
            "SocArXiv: query=%r page=%s returned=%s",
            query[:40], page, len(results)
        )
        return results

    def get_total(self, filter_dict: Dict[str, Any]) -> int:
        """Return total hit count for the current filter."""
        start_date, end_date = get_date_range(filter_dict)
        params = {
            "filter[provider]":          "socarxiv",
            "filter[date_created][gte]": start_date,
            "filter[date_created][lte]": end_date,
            "page[size]": 1,
        }
        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            if resp.ok:
                return resp.json().get("meta", {}).get("total", 0)
        except Exception:
            pass
        return 0

    def get_by_id(self, identifier: str) -> Optional[RawRecord]:
        """Fetch a single preprint by OSF ID or DOI."""
        try:
            resp = self.session.get(
                f"https://api.osf.io/v2/preprints/{identifier}/",
                timeout=self.timeout
            )
            if resp.ok:
                return resp.json().get("data")
        except requests.RequestException:
            pass
        return None

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize an OSF preprint data dict to CanonicalRecord."""
        attrs = raw.get("attributes", {}) or {}
        links = raw.get("links", {}) or {}
        osf_id = raw.get("id", "")

        doi = attrs.get("doi", "") or ""
        title    = attrs.get("title", "") or ""
        abstract = attrs.get("description", "") or ""

        date_raw = attrs.get("date_created", "") or ""
        pub_date = date_raw[:10] if date_raw else ""
        try:
            year = int(pub_date[:4]) if pub_date else 0
        except ValueError:
            year = 0

        authors: List[AuthorRecord] = []
        embedded = raw.get("embeds", {}) or {}
        contributors = embedded.get("contributors", {}).get("data", []) or []
        for i, contrib in enumerate(contributors):
            c_attrs = contrib.get("attributes", {}) or {}
            c_embeds = contrib.get("embeds", {}) or {}
            user_data = c_embeds.get("users", {}).get("data", {}) or {}
            user_attrs = user_data.get("attributes", {}) or {}

            name = user_attrs.get("full_name", "") or c_attrs.get("full_name", "")
            orcid = user_attrs.get("social", {}).get("orcid", "") if isinstance(
                user_attrs.get("social"), dict
            ) else ""

            if name:
                authors.append(AuthorRecord(display_name=name, orcid=orcid, sequence=i + 1))

        first_author = authors[0].display_name.split()[-1] if authors else ""

        subjects_raw = attrs.get("subjects", []) or []
        subjects: List[str] = []
        for s in subjects_raw:
            if isinstance(s, list):
                for item in s:
                    if isinstance(item, dict) and item.get("text"):
                        subjects.append(item["text"])
            elif isinstance(s, dict) and s.get("text"):
                subjects.append(s["text"])

        tags: List[str] = attrs.get("tags", []) or []

        license_info = attrs.get("license", {}) or {}
        license_name = license_info.get("name", "") if isinstance(license_info, dict) else ""

        source_url = links.get("html", "") or f"https://osf.io/preprints/socarxiv/{osf_id}/"
        pdf_url    = links.get("pdf",  "") or ""

        flags = RecordFlags(fulltext_reusable=bool(pdf_url and "cc" in license_name.lower()))

        cid = make_canonical_id(doi=doi, title=title, first_author=first_author, year=year)
        if not doi and not cid:
            cid = f"socarxiv:{osf_id}"

        return CanonicalRecord(
            canonical_id=cid,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            published_date=pub_date,
            document_type="preprint",
            is_preprint=True,
            journal_or_server="SocArXiv",
            doi=doi,
            pmid="",
            pmcid="",
            source_url=source_url,
            best_oa_url=pdf_url or source_url,
            pdf_url=pdf_url,
            license=license_name,
            oa_status="open",
            subjects=subjects[:5],
            keywords=list(tags)[:10],
            source_hits=[SourceHit(
                source=self.source_name,
                source_record_id=osf_id,
                fetched_at=datetime.utcnow().isoformat(),
            )],
            flags=flags,
            source_trust_weight=self.source_trust_weight,
        )
