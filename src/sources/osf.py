"""
OSF preprint adapter, shared by every OSF-hosted server (PsyArXiv, SocArXiv).

Purpose: Search one OSF preprint provider by date range and, where it loses
         nothing, by title term.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B3, #M31
Tests:   tests/test_adapters.py::test_b3_every_or_group_reaches_osf,
         tests/test_adapters.py::test_b3_multiword_term_sent_whole,
         tests/test_adapters.py::test_b3_group_without_title_term_fetches_by_date,
         tests/test_adapters.py::test_b3_terms_over_cap_fall_back_to_date_only

API docs: https://developer.osf.io/

The OSF preprints endpoint has no full-text search. It does accept
`filter[title]`, a contains-match on the title. Sending it is only safe when
every OR group of the filter has a title term: then a paper can only match the
filter through its title, so one request per title term (results merged) drops
nothing the client-side filter would keep. When any group has no title term,
or there are more terms than `osf.max_title_terms` allows, the adapter fetches
by date alone and the client-side filter does all the matching; if the result
limit then cuts the source off, the orchestrator reports it (review B1).
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Sequence, Optional, Dict, Any, List
import logging
import requests

from .base import RawRecord, with_retry
from .schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
from .errors import SourceUnavailableError, RateLimitedError
from .config import polite_user_agent
from .query_builder import get_date_range, osf_title_terms

logger = logging.getLogger(__name__)

BASE_URL = "https://api.osf.io/v2/preprints/"

# Used when sources_config.yaml has no osf.max_title_terms.
DEFAULT_MAX_TITLE_TERMS = 8


class OsfPreprintAdapter:
    """Search adapter for one OSF preprint provider."""

    source_name = ""          # set by each provider subclass
    label = ""                # display name, e.g. "PsyArXiv"
    provider = ""             # OSF provider id, e.g. "psyarxiv"
    source_trust_weight = 0.75

    def __init__(self, timeout: int = 30, sources_config: dict | None = None):
        self.timeout = timeout
        self.sources_config = sources_config or {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": polite_user_agent(self.sources_config),
            "Accept":     "application/vnd.api+json",
        })
        # Per-search state: the orchestrator uses a fresh instance per search
        # (for_search), so none of this is shared between concurrent runs (B6).
        self.last_page_size = 0
        self.last_total = 0
        self._term_totals: Dict[str, int] = {}
        self._exhausted: set = set()
        # Set by the orchestrator: called with a short message before each
        # request, so a page of several term requests shows progress.
        self.on_activity = None

    def for_search(self) -> "OsfPreprintAdapter":
        """A new instance with the same settings, for one search (review B6)."""
        return type(self)(timeout=self.timeout, sources_config=self.sources_config)

    def _max_title_terms(self) -> int:
        osf_cfg = self.sources_config.get("osf") or {}
        try:
            return int(osf_cfg.get("max_title_terms", DEFAULT_MAX_TITLE_TERMS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_TITLE_TERMS

    def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        resp = with_retry(
            lambda: self.session.get(BASE_URL, params=params, timeout=self.timeout),
            source_label=self.label,
        )
        if resp.status_code == 429:
            raise RateLimitedError(f"{self.label} rate limit hit")
        if not resp.ok:
            raise SourceUnavailableError(f"{self.label} returned {resp.status_code}")
        return resp.json()

    def search(
        self, query: str, page: int = 1, page_size: int = 25,
        filter_dict: Optional[Dict[str, Any]] = None,
    ) -> Sequence[RawRecord]:
        """
        Fetch one page of this provider's preprints for the filter's date range.

        Args:
            query:       Informational only; terms are read from filter_dict,
                         because the space-joined query cannot tell "social
                         isolation" from "social" and "isolation".
            page:        1-based page number. Page 1 starts a new search.
            page_size:   Results per page (OSF allows at most 100).
            filter_dict: The filter, for the date range and title terms.

        Returns:
            Raw OSF preprint data dicts. With several title terms, the page is
            the union of each term's page, without repeats.
        """
        fd = filter_dict or {}
        start_date, end_date = get_date_range(fd)
        size = min(page_size, 100)
        base: Dict[str, Any] = {
            "filter[provider]":          self.provider,
            "filter[date_created][gte]": start_date,
            "filter[date_created][lte]": end_date,
            "page[size]":                size,
            "page":                      page,
            "embed":                     "contributors",
        }
        if page == 1:
            self._term_totals = {}
            self._exhausted = set()

        terms = osf_title_terms(fd, self._max_title_terms())
        if not terms:
            data = self._get(base)
            results = data.get("data", []) or []
            self.last_page_size = len(results)
            total = (data.get("meta") or {}).get("total")
            self.last_total = total if isinstance(total, int) else 0
            logger.debug("%s: page=%s returned=%s (date only)", self.label, page, len(results))
            return results

        out: List[RawRecord] = []
        seen_ids: set = set()
        returned = 0
        pending = [t for t in terms if t not in self._exhausted]
        for n, term in enumerate(pending, 1):
            if callable(self.on_activity):
                self.on_activity(f"page {page}, searching title word {n} of {len(pending)}…")
            data = self._get({**base, "filter[title]": term})
            results = data.get("data", []) or []
            total = (data.get("meta") or {}).get("total")
            if isinstance(total, int):
                self._term_totals[term] = total
            if len(results) < size:
                self._exhausted.add(term)
            returned += len(results)
            for r in results:
                rid = r.get("id")
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                out.append(r)
        # What the source sent across the term requests; the orchestrator uses
        # it for page-end detection, and the summed totals as the source total.
        self.last_page_size = returned
        self.last_total = sum(self._term_totals.values())
        logger.debug("%s: page=%s terms=%d returned=%s", self.label, page, len(terms), returned)
        return out

    def get_by_id(self, identifier: str) -> Optional[RawRecord]:
        """Fetch a single preprint by OSF ID or DOI."""
        try:
            resp = with_retry(
                lambda: self.session.get(f"{BASE_URL}{identifier}/", timeout=self.timeout),
                source_label=f"{self.label} get_by_id",
            )
            if resp.ok:
                return resp.json().get("data")
        except Exception as e:
            logger.error("%s get_by_id error: %s", self.label, e)
        return None

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize an OSF preprint data dict to CanonicalRecord."""
        attrs = raw.get("attributes", {}) or {}
        links = raw.get("links", {}) or {}
        osf_id = raw.get("id", "")

        # DOI — OSF preprints use 10.31234/osf.io/...
        doi = attrs.get("doi", "") or ""
        title    = attrs.get("title", "") or ""
        abstract = attrs.get("description", "") or ""

        date_raw = attrs.get("date_created", "") or ""
        pub_date = date_raw[:10] if date_raw else ""
        try:
            year = int(pub_date[:4]) if pub_date else 0
        except ValueError:
            year = 0

        # Authors from embedded contributors
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

        source_url = links.get("html", "") or f"https://osf.io/preprints/{self.provider}/{osf_id}/"
        pdf_url    = links.get("pdf",  "") or ""

        flags = RecordFlags(fulltext_reusable=bool(pdf_url and "cc" in license_name.lower()))

        cid = make_canonical_id(doi=doi, title=title, first_author=first_author, year=year)

        return CanonicalRecord(
            canonical_id=cid,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            published_date=pub_date,
            document_type="preprint",
            is_preprint=True,
            journal_or_server=self.label,
            doi=doi,
            pmid="",
            pmcid="",
            source_url=source_url,
            best_oa_url=pdf_url or source_url,
            pdf_url=pdf_url,
            license=license_name,
            oa_status="open",  # OSF preprint servers are open access
            subjects=subjects[:5],
            # Every tag: the filter matches them (KW2).
            keywords=[t for t in tags if isinstance(t, str) and t.strip()],
            source_hits=[SourceHit(
                source=self.source_name,
                source_record_id=osf_id,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )],
            flags=flags,
            source_trust_weight=self.source_trust_weight,
        )
