"""
Tests for arXiv adapter.
Uses unittest.mock to avoid live network calls.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
import xml.etree.ElementTree as ET


# Minimal Atom XML response fixture
ARXIV_ATOM_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <title>ArXiv Query Results</title>
  <opensearch:totalResults>42</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2301.12345v2</id>
    <title>A Study on Generative Agents and Multi-Agent Simulation</title>
    <summary>This paper examines generative agents in multi-agent simulation environments. The agents adapt and learn over time.</summary>
    <published>2023-01-15T12:34:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Johnson</name></author>
    <category term="cs.AI"/>
    <category term="cs.MA"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2302.67890v1</id>
    <title>Emergent Misalignment in Large Language Models</title>
    <summary>This paper has been withdrawn. Please see version v2 published elsewhere.</summary>
    <published>2023-02-20T08:00:00Z</published>
    <author><name>Carol Williams</name></author>
    <category term="cs.CL"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2303.11111v1</id>
    <title>Algorithmic Fidelity in Agent-Based Models</title>
    <summary>This work investigates the fidelity of algorithmic approximations in agent-based models.</summary>
    <published>2023-03-10T14:20:00Z</published>
    <author><name>David Lee</name></author>
    <category term="cs.SE"/>
  </entry>
</feed>
"""


def test_arxiv_adapter_has_source_name():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()
    assert adapter.source_name == "arxiv"


def test_arxiv_adapter_has_trust_weight():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()
    assert adapter.source_trust_weight == 0.75


def test_arxiv_normalize_produces_canonical():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    raw = {
        "arxiv_id_full": "2301.12345v2",
        "title": "A Study on Generative Agents",
        "abstract": "This paper examines generative agents in simulation.",
        "authors": ["Alice Smith", "Bob Johnson"],
        "published": "2023-01-15",
        "categories": ["cs.AI", "cs.MA"],
    }

    record = adapter.normalize(raw)

    assert record.title == "A Study on Generative Agents"
    assert record.abstract == "This paper examines generative agents in simulation."
    assert record.is_preprint is True
    assert record.document_type == "preprint"
    assert record.journal_or_server == "arXiv"
    assert record.oa_status == "open"
    assert record.published_date == "2023-01-15"
    assert record.year == 2023
    assert record.canonical_id == "arxiv:2301.12345"  # version stripped
    assert record.source_url == "https://arxiv.org/abs/2301.12345v2"
    assert record.pdf_url == "https://arxiv.org/pdf/2301.12345v2"
    assert len(record.authors) == 2
    assert record.authors[0].display_name == "Alice Smith"
    assert record.authors[0].sequence == 1
    assert record.authors[1].display_name == "Bob Johnson"
    assert record.authors[1].sequence == 2
    assert record.subjects == ["cs.AI", "cs.MA"]
    assert record.flags.fulltext_reusable is True
    assert record.source_hits[0].source == "arxiv"


def test_arxiv_normalize_strips_version_from_canonical_id():
    """canonical_id should omit the vN suffix for identity."""
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    raw = {
        "arxiv_id_full": "2301.12345v5",
        "title": "Test",
        "abstract": "Test abstract",
        "authors": [],
        "published": "2023-01-15",
        "categories": [],
    }

    record = adapter.normalize(raw)
    assert record.canonical_id == "arxiv:2301.12345"


def test_arxiv_search_parses_atom_response():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = ARXIV_ATOM_RESPONSE
    mock_resp.headers = {}

    with patch("requests.get", return_value=mock_resp):
        results = adapter.search("ti:agents")

    # Should have 2 entries (3rd one is withdrawn)
    assert len(results) == 2
    assert results[0]["arxiv_id_full"] == "2301.12345v2"
    assert results[1]["arxiv_id_full"] == "2303.11111v1"


def test_arxiv_search_skips_withdrawn_papers():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = ARXIV_ATOM_RESPONSE
    mock_resp.headers = {}

    with patch("requests.get", return_value=mock_resp):
        results = adapter.search("ti:agents")

    # Entry 2 (2302.67890) should be skipped because summary contains "withdrawn"
    arxiv_ids = [r["arxiv_id_full"] for r in results]
    assert "2302.67890v1" not in arxiv_ids


def test_arxiv_search_sets_last_total():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = ARXIV_ATOM_RESPONSE
    mock_resp.headers = {}

    with patch("requests.get", return_value=mock_resp):
        adapter.search("ti:agents")

    assert adapter.last_total == 42


def test_arxiv_search_includes_page_params():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = ARXIV_ATOM_RESPONSE
    mock_resp.headers = {}

    with patch("requests.get", return_value=mock_resp) as mock_get:
        adapter.search("ti:agents", page=2, page_size=50)

    # Check that start and max_results were passed
    call_args = mock_get.call_args
    params = call_args.kwargs.get("params", {})
    assert params["start"] == 50  # (2-1)*50
    assert params["max_results"] == 50


def test_arxiv_search_sends_descriptive_user_agent():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = ARXIV_ATOM_RESPONSE
    mock_resp.headers = {}

    with patch("requests.get", return_value=mock_resp) as mock_get:
        adapter.search("ti:agents")

    call_args = mock_get.call_args
    headers = call_args.kwargs.get("headers", {})
    assert "biorx" in headers.get("User-Agent", "").lower()


def test_arxiv_search_raises_unavailable_on_5xx():
    from src.sources.arxiv import ArxivAdapter
    from src.sources.errors import SourceUnavailableError

    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.raise_for_status.side_effect = Exception("Bad Gateway")

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(SourceUnavailableError):
            adapter.search("ti:agents")


def test_arxiv_search_raises_rate_limited_on_persistent_429():
    from src.sources.arxiv import ArxivAdapter
    from src.sources.errors import RateLimitedError

    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {"Retry-After": "1"}

    with patch("requests.get", return_value=mock_resp):
        with patch("time.sleep"):  # Skip actual sleep
            with pytest.raises(RateLimitedError):
                adapter.search("ti:agents")


def test_arxiv_search_malformed_xml_raises_unavailable():
    from src.sources.arxiv import ArxivAdapter
    from src.sources.errors import SourceUnavailableError

    adapter = ArxivAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"<invalid>xml"
    mock_resp.headers = {}

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(SourceUnavailableError):
            adapter.search("ti:agents")


def test_arxiv_get_by_id_returns_none():
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()
    assert adapter.get_by_id("2301.12345") is None


# ── Withdrawal detection (review finding 3) ───────────────────────────────────
#
# arXiv publishes no machine-readable withdrawn flag, so this is a heuristic on
# the conventional notice. Expected values below come from that convention and
# from ordinary research prose, NOT from the implementation.

WITHDRAWN_PROSE_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Attrition in a Longitudinal Cohort</title>
    <summary>We followed 240 participants over two years. Twelve were withdrawn from the study after week four, and one treatment arm closed early because the drug was withdrawn from sale.</summary>
    <published>2024-01-02T09:00:00Z</published>
    <author><name>Erin Doyle</name></author>
    <category term="stat.AP"/>
  </entry>
</feed>
"""


def _resp(body):
    m = MagicMock()
    m.status_code = 200
    m.content = body
    m.headers = {}
    return m


@pytest.mark.parametrize("summary", [
    "This paper has been withdrawn by the author due to a sign error in Eq. 3.",
    "This submission has been withdrawn.",
    "The manuscript was withdrawn because of an error in the data.",
    "This paper has been withdrawn by the authors, pending further review.",
])
def test_arxiv_withdrawal_notice_is_detected(summary):
    """arXiv's conventional withdrawal notice opens the abstract."""
    from src.sources.arxiv import _is_withdrawn
    assert _is_withdrawn(summary) is True


@pytest.mark.parametrize("summary", [
    "Twelve participants were withdrawn from the study after week four.",
    "The drug was withdrawn from sale in 2011, which we treat as an exogenous shock.",
    "We study how support is withdrawn in adolescent peer networks.",
    "",
])
def test_arxiv_ordinary_prose_about_withdrawal_is_not_a_withdrawn_paper(summary):
    """
    Adversarial (P7): input that contains the trigger word for the wrong reason.
    Expected value comes from the meaning of the sentence, not from the code.
    """
    from src.sources.arxiv import _is_withdrawn
    assert _is_withdrawn(summary) is False


def test_arxiv_search_keeps_papers_that_merely_mention_withdrawal():
    """A real paper about attrition must survive the withdrawn filter."""
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter(min_request_interval=0)

    with patch("requests.get", return_value=_resp(WITHDRAWN_PROSE_RESPONSE)):
        results = adapter.search("ti:attrition")

    assert [r["arxiv_id_full"] for r in results] == ["2401.00001v1"]
    assert adapter.last_dropped_withdrawn == 0


def test_arxiv_search_counts_dropped_withdrawn_papers():
    """Drops must be countable, not silent (P2)."""
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter(min_request_interval=0)

    with patch("requests.get", return_value=_resp(ARXIV_ATOM_RESPONSE)):
        results = adapter.search("ti:agents")

    assert len(results) == 2
    assert adapter.last_dropped_withdrawn == 1


# ── Pagination signal (review finding 2) ──────────────────────────────────────

def test_arxiv_search_reports_the_page_size_the_source_sent():
    """
    last_page_size counts entries BEFORE withdrawn ones are removed, so the
    orchestrator's last-page test isn't fooled by a filtered entry.
    """
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter(min_request_interval=0)

    with patch("requests.get", return_value=_resp(ARXIV_ATOM_RESPONSE)):
        results = adapter.search("ti:agents")

    assert adapter.last_page_size == 3
    assert len(results) == 2


def test_arxiv_normalize_strips_version_from_old_style_identifier():
    from src.sources.arxiv import ArxivAdapter
    rec = ArxivAdapter().normalize({"arxiv_id_full": "cs.CV/0701001v1", "title": "t"})
    assert rec.canonical_id == "arxiv:cs.CV/0701001"
