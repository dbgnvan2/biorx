"""
Contract tests for source adapters.
Uses unittest.mock to avoid real network calls.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock


# ── Europe PMC adapter ────────────────────────────────────────────────────────

EPMC_FIXTURE = {
    "id": "12345678",
    "source": "MED",
    "pmid": "12345678",
    "pmcid": "PMC9876543",
    "doi": "10.1234/test.paper",
    "title": "Maternal stress and cortisol in adolescents",
    "authorString": "Smith J, Jones A",
    "authorList": {"author": [
        {"fullName": "John Smith", "sequence": "first",
         "authorId": {"type": "ORCID", "value": "0000-0001-2345-6789"}},
        {"fullName": "Alice Jones", "sequence": "additional"},
    ]},
    "journalTitle": "Journal of Stress Research",
    "pubYear": "2024",
    "firstPublicationDate": "2024-03-15",
    "abstractText": "This study examines maternal stress and its effects...",
    "isOpenAccess": "Y",
    "license": "CC BY",
    "pubType": "journal article",
    "isPreprint": "N",
    "keywordList": {"keyword": ["stress", "cortisol", "adolescents"]},
}

def test_europepmc_normalize_produces_canonical():
    from src.sources.europepmc import EuropePmcAdapter
    adapter = EuropePmcAdapter()
    record = adapter.normalize(EPMC_FIXTURE)

    assert record.title == "Maternal stress and cortisol in adolescents"
    assert record.doi == "10.1234/test.paper"
    assert record.pmid == "12345678"
    assert record.pmcid == "PMC9876543"
    assert record.abstract == "This study examines maternal stress and its effects..."
    assert record.published_date == "2024-03-15"
    assert record.year == 2024
    assert record.document_type == "article"
    assert record.is_preprint is False
    assert record.license == "CC BY"
    assert record.oa_status == "open"
    assert len(record.authors) == 2
    assert record.authors[0].display_name == "John Smith"
    assert record.authors[0].orcid == "0000-0001-2345-6789"
    assert record.source_hits[0].source == "europepmc"
    assert record.canonical_id.startswith("doi:")


def test_europepmc_normalize_preprint():
    fixture = dict(EPMC_FIXTURE, pubType="preprint", isPreprint="Y",
                   doi="10.1101/preprint.test", pmcid="")
    from src.sources.europepmc import EuropePmcAdapter
    record = EuropePmcAdapter().normalize(fixture)
    assert record.is_preprint is True
    assert record.document_type == "preprint"


def test_europepmc_search_calls_api():
    from src.sources.europepmc import EuropePmcAdapter
    adapter = EuropePmcAdapter()

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "hitCount": 1,
        "nextCursorMark": "ABC",
        "resultList": {"result": [EPMC_FIXTURE]},
    }

    with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
        results = adapter.search("maternal AND FIRST_PDATE:[2025-01-01 TO 2025-03-29]")

    mock_get.assert_called_once()
    assert len(results) == 1
    assert results[0]["doi"] == "10.1234/test.paper"


# ── PsyArXiv adapter ──────────────────────────────────────────────────────────

PSYARXIV_FIXTURE = {
    "id": "abc12",
    "type": "preprints",
    "attributes": {
        "title": "Attachment and emotion regulation in adolescents",
        "description": "This preprint examines attachment theory...",
        "date_created": "2025-03-20T10:00:00Z",
        "doi": "10.31234/osf.io/abc12",
        "tags": ["attachment", "emotion", "adolescents"],
        "subjects": [[{"id": "6012", "text": "Social and Behavioral Sciences"}]],
        "license": {"name": "CC-By Attribution 4.0 International"},
    },
    "links": {
        "html": "https://osf.io/preprints/psyarxiv/abc12/",
        "pdf":  "https://osf.io/abc12/download",
    },
    "embeds": {},
}

def test_psyarxiv_normalize_produces_canonical():
    from src.sources.psyarxiv import PsyArxivAdapter
    adapter = PsyArxivAdapter()
    record  = adapter.normalize(PSYARXIV_FIXTURE)

    assert record.title == "Attachment and emotion regulation in adolescents"
    assert record.doi   == "10.31234/osf.io/abc12"
    assert record.abstract == "This preprint examines attachment theory..."
    assert record.is_preprint is True
    assert record.document_type == "preprint"
    assert record.journal_or_server == "PsyArXiv"
    assert record.oa_status == "open"
    assert record.published_date == "2025-03-20"
    assert record.year == 2025
    assert record.source_hits[0].source == "psyarxiv"
    assert record.pdf_url == "https://osf.io/abc12/download"
    assert "attachment" in record.keywords


def test_psyarxiv_search_calls_osf_api():
    from src.sources.psyarxiv import PsyArxivAdapter
    adapter = PsyArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [PSYARXIV_FIXTURE],
        "meta": {"total": 1},
    }

    with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
        results = adapter.search("attachment", filter_dict={"days_back": 7})

    mock_get.assert_called_once()
    assert len(results) == 1


# ── Crossref adapter ──────────────────────────────────────────────────────────

CROSSREF_FIXTURE = {
    "title": ["Maternal stress in adolescents: a longitudinal study"],
    "abstract": "Extended abstract from Crossref...",
    "author": [
        {"given": "John", "family": "Smith", "ORCID": "http://orcid.org/0000-0001-2345-6789"},
    ],
    "published": {"date-parts": [[2024, 3, 15]]},
    "container-title": ["Journal of Stress Research"],
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
}

def test_crossref_enrich_fills_missing_fields():
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags, make_canonical_id

    adapter = CrossrefAdapter()
    record = CanonicalRecord(
        canonical_id="doi:10.1234/test", title="", abstract="",
        authors=[], year=0, published_date="", document_type="article",
        is_preprint=False, journal_or_server="", doi="10.1234/test",
        pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="", subjects=[], keywords=[],
        source_hits=[], flags=RecordFlags(),
    )

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": CROSSREF_FIXTURE}

    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.enrich(record)

    assert record.title == "Maternal stress in adolescents: a longitudinal study"
    assert record.published_date == "2024-03-15"
    assert record.journal_or_server == "Journal of Stress Research"
    assert len(record.authors) == 1
    assert record.authors[0].display_name == "John Smith"


def test_crossref_strips_jats_xml_from_abstract():
    """Crossref abstracts often contain JATS markup — it must be stripped."""
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags, make_canonical_id

    jats_abstract = (
        "<jats:p>Background: Some background.</jats:p>"
        "<jats:p>Methods: The method.</jats:p>"
        "<jats:p>Results: Findings here.</jats:p>"
    )
    fixture = dict(CROSSREF_FIXTURE, abstract=jats_abstract)

    adapter = CrossrefAdapter()
    cid = make_canonical_id(doi="10.1234/test", title="", first_author="", year=0)
    record = CanonicalRecord(
        canonical_id=cid, title="", abstract="",
        authors=[], year=0, published_date="", document_type="article",
        is_preprint=False, journal_or_server="", doi="10.1234/test",
        pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="",
        license="", oa_status="", subjects=[], keywords=[],
        source_hits=[], flags=RecordFlags(),
    )

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": fixture}

    with patch.object(adapter.session, "get", return_value=mock_resp):
        adapter.enrich(record)

    assert "<jats:" not in record.abstract
    assert "Background:" in record.abstract
    assert "Methods:" in record.abstract
    assert "Results:" in record.abstract


def test_crossref_skips_when_no_doi():
    from src.sources.crossref import CrossrefAdapter
    from src.sources.schema import CanonicalRecord, RecordFlags

    adapter = CrossrefAdapter()
    record = CanonicalRecord(
        canonical_id="title:abc", title="No DOI Paper", abstract="", authors=[],
        year=0, published_date="", document_type="other", is_preprint=False,
        journal_or_server="", doi="", pmid="", pmcid="", source_url="",
        best_oa_url="", pdf_url="", license="", oa_status="", subjects=[],
        keywords=[], source_hits=[], flags=RecordFlags(),
    )

    with patch.object(adapter.session, "get") as mock_get:
        adapter.enrich(record)

    mock_get.assert_not_called()  # no DOI → no Crossref call
