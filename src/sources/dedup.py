"""
Deterministic deduplication across source records.

Identity precedence:
  1. Normalized DOI
  2. PMID
  3. PMCID
  4. Normalized title + first author surname + year

Merge precedence (when two records represent the same work):
  - title:          Europe PMC > Crossref > source-native
  - abstract:       prefer longer non-empty value
  - doi:            Crossref may fill missing
  - best_oa_url:    Unpaywall preferred
  - license:        Unpaywall or PMC preferred
  - is_preprint:    preserve explicitly (don't override True with inferred False)
  - source_hits:    append all
  - trust_weight:   take maximum
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
import hashlib
import re

from .schema import CanonicalRecord, SourceHit

# Source priority for field merging (lower index = higher priority)
_TITLE_PRIORITY = ["europepmc", "crossref", "pubmed"]


def _norm_doi(doi: str) -> str:
    """Normalize a DOI string for comparison."""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def _norm_title(title: str) -> str:
    """Normalize a title string for fuzzy dedup."""
    t = title.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return t


def _title_key(record: CanonicalRecord) -> Optional[str]:
    """Generate the title + first_author + year dedup key."""
    if not record.title:
        return None
    first_author = ""
    if record.authors:
        parts = record.authors[0].display_name.strip().split()
        first_author = parts[-1].lower() if parts else ""
    nt = _norm_title(record.title)
    return f"{nt}|{first_author}|{record.year}"


def _merge_source_priority(a: CanonicalRecord, b: CanonicalRecord, field: str) -> str:
    """Return the higher-priority value for a text field."""
    a_val = getattr(a, field, "") or ""
    b_val = getattr(b, field, "") or ""
    a_source = a.source_hits[0].source if a.source_hits else ""
    b_source = b.source_hits[0].source if b.source_hits else ""

    def _priority(source: str) -> int:
        try:
            return _TITLE_PRIORITY.index(source)
        except ValueError:
            return len(_TITLE_PRIORITY)

    if _priority(a_source) <= _priority(b_source):
        return a_val or b_val
    return b_val or a_val


def _merge(existing: CanonicalRecord, incoming: CanonicalRecord) -> CanonicalRecord:
    """
    Merge incoming record into existing, returning the merged result.
    Prefers existing fields unless incoming has better data.
    """
    # Title: prefer by source priority
    existing.title = _merge_source_priority(existing, incoming, "title") or existing.title or incoming.title

    # Abstract: prefer longer non-empty
    if len(incoming.abstract or "") > len(existing.abstract or ""):
        existing.abstract = incoming.abstract

    # Fill missing identifiers
    if not existing.doi   and incoming.doi:   existing.doi   = incoming.doi
    if not existing.pmid  and incoming.pmid:  existing.pmid  = incoming.pmid
    if not existing.pmcid and incoming.pmcid: existing.pmcid = incoming.pmcid

    # OA fields: prefer Unpaywall/PMC enrichment
    if not existing.best_oa_url and incoming.best_oa_url:
        existing.best_oa_url = incoming.best_oa_url
    if not existing.pdf_url and incoming.pdf_url:
        existing.pdf_url = incoming.pdf_url
    if not existing.oa_status and incoming.oa_status:
        existing.oa_status = incoming.oa_status
    if not existing.license and incoming.license:
        existing.license = incoming.license

    # Preserve is_preprint: once True, always True (preprint status is sticky)
    if incoming.is_preprint:
        existing.is_preprint = True

    # Merge flags
    if incoming.flags.fulltext_reusable:
        existing.flags.fulltext_reusable = True
    if incoming.flags.retracted:
        existing.flags.retracted = True
    if incoming.flags.corrected:
        existing.flags.corrected = True

    # source_hits: append if not already present
    existing_source_ids = {(h.source, h.source_record_id) for h in existing.source_hits}
    for hit in incoming.source_hits:
        if (hit.source, hit.source_record_id) not in existing_source_ids:
            existing.source_hits.append(hit)
            existing_source_ids.add((hit.source, hit.source_record_id))

    # Trust weight: take maximum
    if incoming.source_trust_weight > existing.source_trust_weight:
        existing.source_trust_weight = incoming.source_trust_weight

    # Fill missing metadata
    if not existing.published_date and incoming.published_date:
        existing.published_date = incoming.published_date
    if not existing.year and incoming.year:
        existing.year = incoming.year
    if not existing.journal_or_server and incoming.journal_or_server:
        existing.journal_or_server = incoming.journal_or_server
    if not existing.authors and incoming.authors:
        existing.authors = incoming.authors

    return existing


class Deduplicator:
    """
    Maintains a deduplicated set of CanonicalRecords across multiple sources.

    Usage:
        dedup = Deduplicator()
        for raw in source_results:
            canonical = adapter.normalize(raw)
            dedup.add(canonical)
        records = dedup.results()
    """

    def __init__(self):
        self._records: List[CanonicalRecord] = []
        self._doi_index:   Dict[str, int] = {}   # normalized_doi → index
        self._pmid_index:  Dict[str, int] = {}
        self._pmcid_index: Dict[str, int] = {}
        self._title_index: Dict[str, int] = {}   # title+author+year → index

    def add(self, record: CanonicalRecord) -> CanonicalRecord:
        """
        Add a record, merging into an existing one if a duplicate is detected.

        Returns:
            The canonical record in the collection (merged or newly inserted).
        """
        idx = self._find_existing(record)
        if idx is not None:
            # Merge into existing
            self._records[idx] = _merge(self._records[idx], record)
            # Update all indexes after merge (new identifiers may have been filled)
            self._index_record(self._records[idx], idx)
            return self._records[idx]

        # New record
        idx = len(self._records)
        self._records.append(record)
        self._index_record(record, idx)
        return record

    def _find_existing(self, record: CanonicalRecord) -> Optional[int]:
        """Return index of an existing record that matches, or None."""
        if record.doi:
            nd = _norm_doi(record.doi)
            if nd in self._doi_index:
                return self._doi_index[nd]

        if record.pmid:
            if record.pmid in self._pmid_index:
                return self._pmid_index[record.pmid]

        if record.pmcid:
            if record.pmcid in self._pmcid_index:
                return self._pmcid_index[record.pmcid]

        tk = _title_key(record)
        if tk and tk in self._title_index:
            return self._title_index[tk]

        return None

    def _index_record(self, record: CanonicalRecord, idx: int) -> None:
        """Index a record at the given list position."""
        if record.doi:
            self._doi_index[_norm_doi(record.doi)] = idx
        if record.pmid:
            self._pmid_index[record.pmid] = idx
        if record.pmcid:
            self._pmcid_index[record.pmcid] = idx
        tk = _title_key(record)
        if tk:
            self._title_index[tk] = idx

    def results(self) -> List[CanonicalRecord]:
        """Return all deduplicated records in insertion order."""
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)
