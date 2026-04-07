"""
Europe PMC search adapter.
Primary discovery source for biomedical / human-science literature.
API docs: https://europepmc.org/RestfulWebService
"""

from __future__ import annotations
from datetime import datetime
from typing import Sequence, Optional, Dict, Any, List
import logging
import requests

from .base import RawRecord
from .schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags, make_canonical_id
from .errors import SourceUnavailableError, RateLimitedError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _extract_abstract_from_jats_xml(xml_text: str) -> str:
    """
    Parse a JATS full-text XML string and return the formatted abstract.

    Handles two common patterns:
      1. Structured abstract:  <abstract><sec><title>Methods</title><p>...</p></sec>...</abstract>
      2. Simple abstract:      <abstract><p>...</p></abstract>
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug("JATS XML parse error: %s", e)
        return ""

    # <abstract> may live anywhere under <front><article-meta> or directly under root
    abstract_elem = root.find(".//abstract")
    if abstract_elem is None:
        return ""

    return _format_jats_abstract(abstract_elem)


def _elem_text(elem) -> str:
    """Return all text content of an element and its descendants, joined."""
    return " ".join((elem.itertext())).strip()


def _format_jats_abstract(abstract_elem) -> str:
    """
    Format a JATS <abstract> element into a readable plain-text string.

    Structured abstracts (with <sec> children) are formatted as:
        Section Title
        Section body text.

    Simple abstracts are returned as plain paragraph text.
    """
    import xml.etree.ElementTree as ET

    sections = abstract_elem.findall("sec")

    if sections:
        parts = []
        for sec in sections:
            title_elem = sec.find("title")
            title = title_elem.text.strip() if (title_elem is not None and title_elem.text) else ""

            # Collect text from all <p> elements in this section
            paras = [_elem_text(p) for p in sec.findall(".//p") if _elem_text(p)]
            body  = " ".join(paras)

            if title and body:
                parts.append(f"{title}\n{body}")
            elif body:
                parts.append(body)

        return "\n\n".join(parts)

    # No <sec> children — look for direct <p> elements
    paras = [_elem_text(p) for p in abstract_elem.findall(".//p") if _elem_text(p)]
    if paras:
        return "\n\n".join(paras)

    # Last resort: all text in the element
    return _elem_text(abstract_elem)


class EuropePmcAdapter:
    """Search adapter for the Europe PMC REST API."""

    source_name = "europepmc"
    source_trust_weight = 1.0

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ResearchTool/1.0"})
        # Cursor state per query (reset on each new search call)
        self._cursor_mark: str = "*"
        self._last_query: str = ""

    def search(
        self, query: str, page: int = 1, page_size: int = 25
    ) -> Sequence[RawRecord]:
        """
        Search Europe PMC.

        Args:
            query:     Lucene query string (from query_builder.build_europepmc_query).
            page:      1-based page number. Page 1 resets the cursor.
            page_size: Results per page (max 1000).

        Returns:
            List of raw result dicts from Europe PMC.

        Raises:
            SourceUnavailableError, RateLimitedError
        """
        # Reset cursor when a new query starts
        if query != self._last_query or page == 1:
            self._cursor_mark = "*"
            self._last_query = query

        params = {
            "query":       query,
            "resultType":  "core",
            "pageSize":    min(page_size, 1000),
            "cursorMark":  self._cursor_mark,
            "format":      "json",
        }

        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise SourceUnavailableError(f"Europe PMC unreachable: {e}") from e

        if resp.status_code == 429:
            raise RateLimitedError("Europe PMC rate limit hit")
        if not resp.ok:
            raise SourceUnavailableError(f"Europe PMC returned {resp.status_code}")

        data = resp.json()
        # Advance cursor for next page
        self._cursor_mark = data.get("nextCursorMark", "*")

        results = data.get("resultList", {}).get("result", [])
        logger.debug(
            "Europe PMC: query=%r hit_count=%s page=%s returned=%s",
            query[:60], data.get("hitCount"), page, len(results)
        )
        return results

    def get_total(self, query: str) -> int:
        """Return the total hit count for a query without fetching all results."""
        params = {"query": query, "resultType": "idlist", "pageSize": 1, "format": "json"}
        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            if resp.ok:
                return int(resp.json().get("hitCount", 0))
        except Exception:
            pass
        return 0

    def get_by_id(self, identifier: str) -> Optional[RawRecord]:
        """Fetch a single record by PMID or DOI."""
        # Determine query form
        if identifier.startswith("10."):
            query = f'DOI:"{identifier}"'
        else:
            query = f"EXT_ID:{identifier} AND SRC:MED"

        params = {"query": query, "resultType": "core", "pageSize": 1, "format": "json"}
        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            if resp.ok:
                results = resp.json().get("resultList", {}).get("result", [])
                return results[0] if results else None
        except requests.RequestException as e:
            logger.error("Europe PMC get_by_id error: %s", e)
        return None

    def fetch_abstract_from_fulltext(self, pmcid: str) -> str:
        """
        Fetch and parse the abstract from Europe PMC full-text XML.

        Called when the search API returns an empty abstractText but the paper
        has a PMCID (and therefore a full-text XML available).
        The abstract is parsed from JATS <abstract> elements, including
        structured abstracts with <sec> subsections.

        Returns:
            Formatted abstract string, or "" on failure.
        """
        if not pmcid:
            return ""
        # Normalise: ensure PMC prefix
        pmcid_clean = pmcid.upper()
        if not pmcid_clean.startswith("PMC"):
            pmcid_clean = f"PMC{pmcid_clean}"

        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid_clean}/fullTextXML"
        try:
            resp = self.session.get(url, timeout=20)
            if not resp.ok:
                logger.debug("Full-text XML not available for %s: %s", pmcid_clean, resp.status_code)
                return ""
        except requests.RequestException as e:
            logger.debug("Full-text XML fetch failed for %s: %s", pmcid_clean, e)
            return ""

        return _extract_abstract_from_jats_xml(resp.text)

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize a Europe PMC raw result dict to CanonicalRecord."""
        doi   = raw.get("doi", "") or ""
        pmid  = raw.get("pmid", "") or ""
        pmcid = raw.get("pmcid", "") or ""

        # Authors
        authors: List[AuthorRecord] = []
        author_list = raw.get("authorList", {}).get("author", [])
        if author_list:
            for i, a in enumerate(author_list):
                display = (
                    a.get("fullName")
                    or f"{a.get('lastName', '')} {a.get('firstName', '')}".strip()
                    or a.get("collectiveName", "")
                )
                authors.append(AuthorRecord(
                    display_name=display,
                    orcid=a.get("authorId", {}).get("value", "") if isinstance(a.get("authorId"), dict) else "",
                    sequence=i + 1,
                ))
        elif raw.get("authorString"):
            for i, name in enumerate(raw["authorString"].split(",")):
                authors.append(AuthorRecord(display_name=name.strip(), sequence=i + 1))

        first_author = authors[0].display_name.split()[-1] if authors else ""

        # Date
        pub_date = raw.get("firstPublicationDate", "") or ""
        year_raw = raw.get("pubYear", "")
        try:
            year = int(year_raw) if year_raw else (int(pub_date[:4]) if pub_date else 0)
        except (ValueError, IndexError):
            year = 0

        # Document type / preprint flag
        pub_type = (raw.get("pubType") or "").lower()
        is_preprint = (
            raw.get("isPreprint", "N") == "Y"
            or "preprint" in pub_type
        )
        if is_preprint:
            doc_type = "preprint"
        elif "review" in pub_type:
            doc_type = "review"
        elif "clinical trial" in pub_type:
            doc_type = "trial"
        elif pub_type in ("journal article", "research-article"):
            doc_type = "article"
        else:
            doc_type = "other"

        # Trust weight: peer-reviewed gets 1.0, PMC-backed gets 0.98
        source_field = raw.get("source", "")
        trust = 0.98 if source_field == "PMC" else 1.0

        # Keywords and subjects
        keywords: List[str] = raw.get("keywordList", {}).get("keyword", []) or []
        mesh_list = raw.get("meshHeadingList", {}).get("meshHeading", []) or []
        subjects: List[str] = [m.get("descriptorName", "") for m in mesh_list if m.get("descriptorName")]

        # OA
        oa_status = "open" if raw.get("isOpenAccess", "N") == "Y" else "closed"
        license_  = raw.get("license", "") or ""

        # URLs
        pmcid_clean = pmcid.replace("PMC", "") if pmcid else ""
        best_oa_url = (
            f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
            if pmcid else ""
        )
        source_url = (
            f"https://europepmc.org/article/med/{pmid}"
            if pmid else
            f"https://doi.org/{doi}" if doi else ""
        )

        flags = RecordFlags(fulltext_reusable=bool(pmcid and oa_status == "open"))

        cid = make_canonical_id(doi=doi, pmid=pmid, pmcid=pmcid,
                                title=raw.get("title", ""),
                                first_author=first_author, year=year)

        return CanonicalRecord(
            canonical_id=cid,
            title=raw.get("title", "") or "",
            abstract=raw.get("abstractText", "") or "",
            authors=authors,
            year=year,
            published_date=pub_date,
            document_type=doc_type,
            is_preprint=is_preprint,
            journal_or_server=raw.get("journalTitle", "") or "",
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            source_url=source_url,
            best_oa_url=best_oa_url,
            pdf_url="",
            license=license_,
            oa_status=oa_status,
            subjects=subjects[:5],
            keywords=list(keywords)[:10],
            source_hits=[SourceHit(
                source=self.source_name,
                source_record_id=raw.get("id", doi or pmid),
                fetched_at=datetime.utcnow().isoformat(),
            )],
            flags=flags,
            source_trust_weight=trust,
        )
