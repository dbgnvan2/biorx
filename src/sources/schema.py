"""
Canonical record schema for the publication source layer.
All source adapters normalize their raw API responses to CanonicalRecord.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import hashlib


@dataclass
class AuthorRecord:
    display_name: str
    orcid: str = ""
    sequence: int = 1


@dataclass
class SourceHit:
    source: str           # 'europepmc' | 'psyarxiv' | 'biorxiv_medrxiv' | etc.
    source_record_id: str
    fetched_at: str       # ISO datetime string


@dataclass
class RecordFlags:
    retracted: bool = False
    corrected: bool = False
    fulltext_reusable: bool = False


@dataclass
class CanonicalRecord:
    canonical_id: str
    title: str
    abstract: str
    authors: List[AuthorRecord]
    year: int
    published_date: str                # YYYY-MM-DD
    document_type: str                 # 'article' | 'preprint' | 'review' | 'trial' | 'other'
    is_preprint: bool
    journal_or_server: str
    doi: str
    pmid: str
    pmcid: str
    source_url: str
    best_oa_url: str
    pdf_url: str
    license: str
    oa_status: str
    subjects: List[str]
    keywords: List[str]
    source_hits: List[SourceHit]
    flags: RecordFlags
    source_trust_weight: float = 1.0

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a plain dict compatible with the GUI's _filter_papers()."""
        authors_str = "; ".join(a.display_name for a in self.authors)
        return {
            # Fields expected by _filter_papers()
            "title":                          self.title,
            "abstract":                       self.abstract,
            "authors":                        authors_str,
            "author_corresponding":           self.authors[0].display_name if self.authors else "",
            "author_corresponding_institution": "",
            "type":                           self.document_type,
            "version":                        "1",
            "published":                      "NA" if self.is_preprint else self.journal_or_server or "published",
            "license":                        self.license,
            # Display / metadata fields
            "date":                           self.published_date,
            "pub_date":                       self.published_date,
            "doi":                            self.doi,
            "pmid":                           self.pmid,
            "pmcid":                          self.pmcid,
            "category":                       self.subjects[0] if self.subjects else "",
            "journal_or_server":              self.journal_or_server,
            "url":                            self.source_url,
            "source_url":                     self.source_url,
            "best_oa_url":                    self.best_oa_url,
            "pdf_url":                        self.pdf_url,
            "oa_status":                      self.oa_status,
            "is_preprint":                    self.is_preprint,
            "canonical_id":                   self.canonical_id,
            "source":                         self.source_hits[0].source if self.source_hits else "",
            "source_trust_weight":            self.source_trust_weight,
            "flags":                          {
                "retracted": self.flags.retracted,
                "corrected": self.flags.corrected,
                "fulltext_reusable": self.flags.fulltext_reusable,
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CanonicalRecord":
        """Reconstruct a CanonicalRecord from a plain dict (e.g. stored in DB)."""
        authors = []
        raw_authors = d.get("authors", "")
        if isinstance(raw_authors, list):
            for a in raw_authors:
                if isinstance(a, dict):
                    authors.append(AuthorRecord(**a))
                else:
                    authors.append(AuthorRecord(display_name=str(a)))
        elif isinstance(raw_authors, str) and raw_authors:
            for i, name in enumerate(raw_authors.split(";")):
                authors.append(AuthorRecord(display_name=name.strip(), sequence=i + 1))

        hits = []
        for h in d.get("source_hits", []):
            if isinstance(h, dict):
                hits.append(SourceHit(**h))

        flags_raw = d.get("flags", {}) or {}
        flags = RecordFlags(
            retracted=flags_raw.get("retracted", False),
            corrected=flags_raw.get("corrected", False),
            fulltext_reusable=flags_raw.get("fulltext_reusable", False),
        )

        return cls(
            canonical_id=d.get("canonical_id", ""),
            title=d.get("title", ""),
            abstract=d.get("abstract", ""),
            authors=authors,
            year=d.get("year", 0),
            published_date=d.get("published_date") or d.get("pub_date") or d.get("date", ""),
            document_type=d.get("document_type") or d.get("type", "other"),
            is_preprint=d.get("is_preprint", False),
            journal_or_server=d.get("journal_or_server", ""),
            doi=d.get("doi", ""),
            pmid=d.get("pmid", ""),
            pmcid=d.get("pmcid", ""),
            source_url=d.get("source_url") or d.get("url", ""),
            best_oa_url=d.get("best_oa_url", ""),
            pdf_url=d.get("pdf_url", ""),
            license=d.get("license", ""),
            oa_status=d.get("oa_status", ""),
            subjects=d.get("subjects", []),
            keywords=d.get("keywords", []),
            source_hits=hits,
            flags=flags,
            source_trust_weight=d.get("source_trust_weight", 1.0),
        )


# ── Canonical ID generation ────────────────────────────────────────────────────

def make_canonical_id(doi: str = "", pmid: str = "", pmcid: str = "",
                      title: str = "", first_author: str = "", year: int = 0) -> str:
    """Generate a stable canonical ID using the first available identifier."""
    if doi:
        return f"doi:{_norm_doi(doi)}"
    if pmid:
        return f"pmid:{pmid.strip()}"
    if pmcid:
        return f"pmcid:{pmcid.strip()}"
    # Fallback: title + first author + year fingerprint
    key = f"{_norm_title(title)}|{first_author.lower().strip()}|{year}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"title:{digest}"


def _norm_doi(doi: str) -> str:
    """Normalize DOI: lowercase, strip URL prefix."""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def _norm_title(title: str) -> str:
    """Normalize title for fuzzy matching."""
    import re
    t = title.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return t
