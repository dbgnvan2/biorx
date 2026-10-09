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


# ── M22: the title key's surname is read the same way for every source ───────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M22
# The key took the last word of display_name: Europe PMC's "Smith J" gave "j",
# bioRxiv's "Smith, J." gave "j.", arXiv's "John Smith" gave "smith", so the
# same paper from two sources was never merged by title.

def _raw_epmc(title):
    return {"title": title, "pubYear": "2024", "pubType": "preprint",
            "authorList": {"author": [{"fullName": "Smith J", "lastName": "Smith",
                                       "firstName": "John"}]}}


def test_m22_cross_source_surname_match():
    from src.sources.dedup import Deduplicator
    from src.sources.europepmc import EuropePmcAdapter
    from src.sources.arxiv import ArxivAdapter
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter

    title = "Agents That Simulate Societies"
    epmc = EuropePmcAdapter().normalize(_raw_epmc(title))
    arx = ArxivAdapter.__new__(ArxivAdapter).normalize({
        "arxiv_id_full": "2401.00001v1", "title": title, "authors": ["John Smith"],
        "published": "2024-01-02"})
    bx = BiorxivMedrxivAdapter.__new__(BiorxivMedrxivAdapter).normalize({
        "title": title, "authors": "Smith, J.; Doe, A.", "pub_date": "2024-01-03",
        "server": "biorxiv"})
    d = Deduplicator()
    for r in (epmc, arx, bx):
        d.add(r)
    assert len(d) == 1, [r.source_hits[0].source for r in d.results()]


def test_m22_different_title_not_merged():
    """Adversarial: same surname and year, different paper."""
    from src.sources.dedup import Deduplicator
    from src.sources.europepmc import EuropePmcAdapter
    d = Deduplicator()
    d.add(EuropePmcAdapter().normalize(_raw_epmc("Agents That Simulate Societies")))
    d.add(EuropePmcAdapter().normalize(_raw_epmc("Agents That Simulate Markets")))
    assert len(d) == 2


def test_m22_arxiv_record_carries_its_datacite_doi():
    from src.sources.arxiv import ArxivAdapter
    r = ArxivAdapter.__new__(ArxivAdapter).normalize({
        "arxiv_id_full": "2401.00001v2", "title": "T"})
    assert r.doi == "10.48550/arXiv.2401.00001"
    assert r.canonical_id == "arxiv:2401.00001"     # stored references unchanged


def test_t11_multi_word_surname_merges_across_sources():
    """Plan 2026-09-29 T1.1: Europe PMC's lastName "da Silva" keyed "dasilva",
    arXiv's "Ana da Silva" keyed "silva" — the same paper never merged."""
    from src.sources.dedup import Deduplicator
    from src.sources.europepmc import EuropePmcAdapter
    from src.sources.arxiv import ArxivAdapter
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter

    title = "Agents That Simulate Societies"
    raw = _raw_epmc(title)
    raw["authorList"]["author"] = [{"fullName": "da Silva A", "lastName": "da Silva",
                                    "firstName": "Ana"}]
    epmc = EuropePmcAdapter().normalize(raw)
    arx = ArxivAdapter.__new__(ArxivAdapter).normalize({
        "arxiv_id_full": "2401.00001v1", "title": title, "authors": ["Ana da Silva"],
        "published": "2024-01-02"})
    bx = BiorxivMedrxivAdapter.__new__(BiorxivMedrxivAdapter).normalize({
        "title": title, "authors": "da Silva, A.; Doe, B.", "pub_date": "2024-01-03",
        "server": "biorxiv"})
    d = Deduplicator()
    for r in (epmc, arx, bx):
        d.add(r)
    assert len(d) == 1, [r.source_hits[0].source for r in d.results()]


def test_t11_da_silva_and_silva_are_not_merged():
    """QA gate 2026-09-30 F1 (the plan's own adversarial case): Europe PMC
    "da Silva" and arXiv "Ana Silva", same title and year, are two authors."""
    from src.sources.dedup import Deduplicator
    from src.sources.europepmc import EuropePmcAdapter
    from src.sources.arxiv import ArxivAdapter
    raw = _raw_epmc("Agents That Simulate Societies")
    raw["authorList"]["author"] = [{"fullName": "da Silva A", "lastName": "da Silva",
                                    "firstName": "Ana"}]
    d = Deduplicator()
    d.add(EuropePmcAdapter().normalize(raw))
    d.add(ArxivAdapter.__new__(ArxivAdapter).normalize({
        "arxiv_id_full": "2401.00001v1", "title": "Agents That Simulate Societies",
        "authors": ["Ana Silva"], "published": "2024-01-02"}))
    assert len(d) == 2


@pytest.mark.parametrize("family,display,same", [
    ("da Silva", "Ana da Silva", True),
    ("van der Berg", "Jan van der Berg", True),
    ("da Silva", "Ana Silva", False),
    ("de la Cruz", "Maria Cruz", False),
    ("Smith", "John Smith", True),
])
def test_t11_surname_keys(family, display, same):
    from types import SimpleNamespace
    from src.sources.dedup import _surname
    a = _surname(SimpleNamespace(family=family, display_name=""))
    b = _surname(SimpleNamespace(family="", display_name=display))
    assert (a == b) is same, (a, b)


def test_t11_same_title_other_first_author_not_merged():
    """Adversarial: same title and year, first author Silva vs Souza."""
    from src.sources.dedup import Deduplicator
    from src.sources.arxiv import ArxivAdapter
    a = ArxivAdapter.__new__(ArxivAdapter)
    d = Deduplicator()
    d.add(a.normalize({"arxiv_id_full": "2401.00001v1", "title": "Agents",
                       "authors": ["Ana da Silva"], "published": "2024-01-02"}))
    d.add(a.normalize({"arxiv_id_full": "2401.00002v1", "title": "Agents",
                       "authors": ["Ana de Souza"], "published": "2024-01-02"}))
    assert len(d) == 2


# ── KW3: a merged paper keeps every source's keywords ────────────────────────
# docs/implementation_plan_2026-10-08_author_keywords.md#KW3

@pytest.mark.parametrize("first", ["biorxiv_medrxiv", "europepmc"])
def test_kw3_keywords_merged(first):
    epmc = _make(doi="10.1/x", source="europepmc")
    epmc.keywords = ["Internal Family Systems", "parts"]
    bio = _make(doi="10.1/x", source="biorxiv_medrxiv")
    bio.keywords = []
    other = _make(doi="10.1/x", source="pubmed")
    other.keywords = ["internal family systems", "self-leadership"]
    d = Deduplicator()
    order = [bio, epmc] if first == "biorxiv_medrxiv" else [epmc, bio]
    for r in order + [other]:
        d.add(r)
    (merged,) = d.results()
    assert merged.keywords == ["Internal Family Systems", "parts", "self-leadership"]


def test_gn2_merged_keywords_stripped():
    from src.sources.dedup import merged_keywords
    assert merged_keywords([" Parts "], ["parts", "  Self  ", None, 3]) == ["Parts", "Self"]
