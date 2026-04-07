"""Tests for dedup.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.sources.dedup import Deduplicator
from src.sources.schema import CanonicalRecord, AuthorRecord, SourceHit, RecordFlags


def _make(doi="", pmid="", pmcid="", title="Test Paper", author="Smith",
          year=2024, source="europepmc", abstract=""):
    from src.sources.schema import make_canonical_id
    cid = make_canonical_id(doi=doi, pmid=pmid, pmcid=pmcid, title=title,
                            first_author=author, year=year)
    return CanonicalRecord(
        canonical_id=cid,
        title=title,
        abstract=abstract,
        authors=[AuthorRecord(display_name=f"{author} A", sequence=1)],
        year=year,
        published_date=f"{year}-01-01",
        document_type="article",
        is_preprint=False,
        journal_or_server="Test Journal",
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        source_url="https://example.com",
        best_oa_url="",
        pdf_url="",
        license="cc_by",
        oa_status="open",
        subjects=[],
        keywords=[],
        source_hits=[SourceHit(source=source, source_record_id=doi or pmid, fetched_at="2024-01-01")],
        flags=RecordFlags(),
        source_trust_weight=1.0,
    )


def test_same_doi_deduplicates():
    d = Deduplicator()
    r1 = _make(doi="10.1234/test", source="europepmc")
    r2 = _make(doi="10.1234/test", source="psyarxiv")
    d.add(r1)
    d.add(r2)
    assert len(d) == 1
    result = d.results()[0]
    assert len(result.source_hits) == 2


def test_different_doi_no_dedup():
    d = Deduplicator()
    d.add(_make(doi="10.1234/a", title="Paper Alpha", author="Adams"))
    d.add(_make(doi="10.1234/b", title="Paper Beta",  author="Baker"))
    assert len(d) == 2


def test_pmid_dedup_when_no_doi():
    d = Deduplicator()
    r1 = _make(pmid="12345678", title="Paper A")
    r2 = _make(pmid="12345678", title="Paper A")
    d.add(r1)
    d.add(r2)
    assert len(d) == 1


def test_title_author_year_fallback_dedup():
    d = Deduplicator()
    r1 = _make(title="Stress and cortisol", author="Smith", year=2024)
    r2 = _make(title="Stress and cortisol", author="Smith", year=2024)
    d.add(r1)
    d.add(r2)
    assert len(d) == 1


def test_different_year_no_dedup():
    d = Deduplicator()
    d.add(_make(title="Stress", author="Smith", year=2023))
    d.add(_make(title="Stress", author="Smith", year=2024))
    assert len(d) == 2


def test_merge_prefers_longer_abstract():
    d = Deduplicator()
    r1 = _make(doi="10.1234/x", abstract="Short abstract.")
    r2 = _make(doi="10.1234/x", abstract="A much longer abstract with more information about the paper.")
    d.add(r1)
    d.add(r2)
    assert "longer" in d.results()[0].abstract


def test_merge_is_preprint_sticky():
    """Once is_preprint is True for a record, merging a non-preprint keeps it True."""
    d = Deduplicator()
    r1 = _make(doi="10.1234/x")
    r1.is_preprint = True
    r2 = _make(doi="10.1234/x")
    r2.is_preprint = False
    d.add(r1)
    d.add(r2)
    assert d.results()[0].is_preprint is True


def test_doi_normalized_for_dedup():
    """https://doi.org/ prefix should not prevent dedup."""
    d = Deduplicator()
    r1 = _make(doi="10.1234/norm")
    r2 = _make(doi="https://doi.org/10.1234/norm")
    d.add(r1)
    d.add(r2)
    assert len(d) == 1


def test_source_hits_appended():
    d = Deduplicator()
    r1 = _make(doi="10.1234/y", source="europepmc")
    r2 = _make(doi="10.1234/y", source="psyarxiv")
    d.add(r1)
    d.add(r2)
    sources = {h.source for h in d.results()[0].source_hits}
    assert "europepmc" in sources
    assert "psyarxiv" in sources


def test_trust_weight_takes_maximum():
    d = Deduplicator()
    r1 = _make(doi="10.1234/z", source="europepmc")
    r1.source_trust_weight = 1.0
    r2 = _make(doi="10.1234/z", source="psyarxiv")
    r2.source_trust_weight = 0.75
    d.add(r2)
    d.add(r1)
    assert d.results()[0].source_trust_weight == 1.0
