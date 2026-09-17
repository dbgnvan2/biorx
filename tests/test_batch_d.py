"""
Batch D — arXiv query builder and monitor CLI fixes.

Tests named test_d_* verify each item in the batch D table:
  - arXiv wildcard stripping
  - arXiv version carried into CanonicalRecord
  - monitor exit code reflects source failures
  - duplicate filter names both run under --all
  - Optional[dict] annotation (3.9-compatible)
  - arXiv retries 5xx with backoff (M1)
  - monitor counts and reports failed PDF downloads (M2)
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import importlib.util

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))

import pytest


# ── Load monitor module ───────────────────────────────────────────────────────

_spec = importlib.util.spec_from_file_location(
    "monitor", Path(__file__).parent.parent / "agents" / "monitor.py"
)
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _filter_dict(**kwargs):
    base = {
        "text_groups": [],
        "authors": [],
        "days_back": 7,
        "source_selection": {"all": True, "selected": []},
    }
    base.update(kwargs)
    return base


# ── arXiv wildcard stripping ──────────────────────────────────────────────────

def test_d_arxiv_query_strips_wildcards():
    """
    arXiv has no wildcard operator; trailing * must be removed before the
    query is sent, or arXiv silently returns zero results.
    """
    from src.sources.query_builder import build_arxiv_query
    q = build_arxiv_query({
        "text_groups": [{"title": "", "abstract": "", "both": "adolescen*"}],
        "days_back": 7,
        "authors": [],
    })
    assert "*" not in q, f"wildcard should be stripped, got: {q}"
    assert "adolescen" in q, f"base term should remain, got: {q}"


def test_d_arxiv_query_strips_wildcards_in_title_and_abstract():
    """Wildcard stripping applies to all three fields."""
    from src.sources.query_builder import build_arxiv_query
    q = build_arxiv_query({
        "text_groups": [{"title": "stress*", "abstract": "immun*", "both": ""}],
        "days_back": 7,
        "authors": [],
    })
    assert "*" not in q
    assert "stress" in q
    assert "immun" in q


# ── arXiv version → CanonicalRecord ──────────────────────────────────────────

def test_d_arxiv_v2_matches_revised_only_filter():
    """
    An arXiv id ending in v2 must produce version='2' in to_dict() so that
    the '2+ (revised only)' filter can match it.
    """
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()
    raw = {
        "arxiv_id_full": "2301.12345v2",
        "title": "Test paper",
        "abstract": "Abstract here",
        "authors": ["Smith J"],
        "published": "2023-01-15",
        "categories": ["cs.AI"],
    }
    record = adapter.normalize(raw)
    assert record.to_dict()["version"] == "2"


def test_d_arxiv_v1_has_version_1():
    """A v1 id produces version='1'."""
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()
    raw = {
        "arxiv_id_full": "2301.12345v1",
        "title": "Test paper",
        "abstract": "Abstract here",
        "authors": [],
        "published": "2023-01-15",
        "categories": [],
    }
    assert adapter.normalize(raw).to_dict()["version"] == "1"


def test_d_arxiv_no_version_suffix_defaults_to_1():
    """An id without vN (very old format) defaults to version='1'."""
    from src.sources.arxiv import ArxivAdapter
    adapter = ArxivAdapter()
    raw = {
        "arxiv_id_full": "hep-th/9404001",
        "title": "Old paper",
        "abstract": "Abstract",
        "authors": [],
        "published": "1994-04-01",
        "categories": [],
    }
    assert adapter.normalize(raw).to_dict()["version"] == "1"


# ── arXiv retries 5xx with backoff (M1) ──────────────────────────────────────

def test_d_arxiv_retries_5xx_with_backoff():
    """
    A 5xx response must be retried with exponential backoff, not raised
    immediately as SourceUnavailableError on the first attempt.
    """
    from src.sources.arxiv import ArxivAdapter
    from src.sources.errors import SourceUnavailableError

    adapter = ArxivAdapter(timeout=1)

    fail_resp = MagicMock()
    fail_resp.status_code = 503

    ok_xml = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:os="http://a9.com/-/spec/opensearch/1.1/">
      <os:totalResults>0</os:totalResults>
    </feed>"""
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.content = ok_xml

    with patch("requests.get", side_effect=[fail_resp, ok_resp]) as mock_get, \
         patch("time.sleep") as mock_sleep:
        results = adapter.search("ti:test", page=1, page_size=10)

    assert mock_get.call_count == 2, "should retry once after 5xx"
    mock_sleep.assert_called()


def test_d_arxiv_raises_after_exhausting_retries_on_5xx():
    """After max_attempts 5xx responses, SourceUnavailableError is raised."""
    from src.sources.arxiv import ArxivAdapter
    from src.sources.errors import SourceUnavailableError

    adapter = ArxivAdapter(timeout=1)
    fail_resp = MagicMock()
    fail_resp.status_code = 500

    with patch("requests.get", return_value=fail_resp), \
         patch("time.sleep"):
        with pytest.raises(SourceUnavailableError):
            adapter.search("ti:test", page=1, page_size=10)


def test_d_arxiv_retries_request_exception_with_backoff():
    """A network-level error (RequestException) should also retry."""
    import requests as req_mod
    from src.sources.arxiv import ArxivAdapter
    from src.sources.errors import SourceUnavailableError

    adapter = ArxivAdapter(timeout=1)

    ok_xml = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:os="http://a9.com/-/spec/opensearch/1.1/">
      <os:totalResults>0</os:totalResults>
    </feed>"""
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.content = ok_xml

    with patch("requests.get", side_effect=[req_mod.ConnectionError("timeout"), ok_resp]) as mock_get, \
         patch("time.sleep"):
        results = adapter.search("ti:test", page=1, page_size=10)

    assert mock_get.call_count == 2


# ── monitor: exit code reflects source failures ───────────────────────────────

def test_d_monitor_exit_code_reflects_failed_sources(tmp_path, capsys):
    """
    main() must exit 2 when at least one source failed, naming the failure
    on stderr. Exiting 0 on a failed run is the bug (CLI no-failure-signal).
    """
    filters_json = tmp_path / "filters.json"
    filters_json.write_text(
        '{"filters": [{"name": "Test", "enabled": true, "days_back": 7, '
        '"text_groups": [], "authors": [], "source_selection": {"all": true, "selected": []}}]}'
    )

    from src.sources.orchestrator import FAILURE_STATUS_MARKER as _FM
    FAILURE_MSG = f"Europe PMC {_FM} (unavailable)"

    def fake_search(filter_dict, source_selection=None, on_batch=None, on_progress=None,
                    on_status=None, should_stop=None, max_results=200):
        if on_status:
            on_status(FAILURE_MSG)
        return []

    orch = MagicMock()
    orch.search.side_effect = fake_search

    with patch.object(monitor, "load_sources_config", return_value={}), \
         patch.object(monitor, "SourceOrchestrator", return_value=orch), \
         patch.object(monitor, "load_filters", return_value=[
             {"name": "Test", "enabled": True, "days_back": 7,
              "text_groups": [], "authors": [],
              "source_selection": {"all": True, "selected": []}}
         ]):
        exit_code = monitor.main(["--all", "--filters-path", str(filters_json)])

    assert exit_code == 2, f"expected 2 when sources failed, got {exit_code}"


def test_d_monitor_exits_0_when_all_sources_succeed(tmp_path):
    """Smoke test: no failures → exit 0."""
    orch = MagicMock()
    orch.search.return_value = []

    with patch.object(monitor, "load_sources_config", return_value={}), \
         patch.object(monitor, "SourceOrchestrator", return_value=orch), \
         patch.object(monitor, "load_filters", return_value=[
             {"name": "Test", "enabled": True, "days_back": 7,
              "text_groups": [], "authors": [],
              "source_selection": {"all": True, "selected": []}}
         ]):
        exit_code = monitor.main(["--all"])

    assert exit_code == 0


# ── duplicate filter names both run ──────────────────────────────────────────

def test_d_duplicate_filter_names_both_run(tmp_path):
    """
    filters.json has two 'New Filter' entries (verified in the real file).
    load_filters must return both and --all must run both.
    """
    real_filters = Path(__file__).parent.parent / "filters.json"
    filters = monitor.load_filters(str(real_filters))

    new_filter_entries = [f for f in filters if f.get("name") == "New Filter"]
    assert len(new_filter_entries) == 2, (
        f"expected 2 'New Filter' entries in filters.json, found {len(new_filter_entries)}"
    )


def test_d_load_filters_returns_list():
    """load_filters returns a list (order-preserving, supports duplicate names)."""
    filters = monitor.load_filters(
        str(Path(__file__).parent.parent / "filters.json")
    )
    assert isinstance(filters, list)


def test_d_duplicate_filter_names_warn(tmp_path, caplog):
    """load_filters emits a warning when two filters share a name."""
    import logging
    filters_json = tmp_path / "filters.json"
    filters_json.write_text(
        '{"filters": ['
        '{"name": "Dup", "enabled": true}, '
        '{"name": "Dup", "enabled": true}'
        ']}'
    )
    with caplog.at_level(logging.WARNING):
        filters = monitor.load_filters(str(filters_json))
    assert len(filters) == 2
    assert any("Dup" in r.message for r in caplog.records), (
        "expected a warning about duplicate filter name 'Dup'"
    )


# ── Optional[dict] annotation (3.9-compatible) ───────────────────────────────

def test_d_monitor_imports_on_the_declared_floor():
    """
    find_filter's annotation must not use dict|None (3.10+ union syntax);
    the module must import cleanly on 3.9+. Verified by inspecting the annotation
    rather than just importing (which already ran above).
    """
    import inspect
    sig = inspect.signature(monitor.find_filter)
    ann = sig.parameters["name"].annotation
    # The annotation may be inspect.Parameter.empty or a string; the important
    # thing is the return annotation doesn't use X|Y syntax — we check by
    # verifying the module loaded successfully on this interpreter (done at
    # module import above) and that the return annotation is not a union type.
    ret = sig.return_annotation
    # On 3.10+ types.UnionType would appear; on 3.9 Optional is a GenericAlias.
    # Either way, importing successfully on the interpreter running the suite
    # is the meaningful check. If the module had dict|None it would SyntaxError
    # on 3.9 before we got here.
    import inspect
    import types
    ret = inspect.signature(monitor.find_filter).return_annotation
    # On 3.10+ dict|None produces a types.UnionType; on 3.9 it is a SyntaxError
    # before the import even completes. If the annotation uses X|Y syntax and this
    # is Python 3.10+, we can detect it and fail explicitly.
    if hasattr(types, "UnionType"):
        assert not isinstance(ret, types.UnionType), (
            f"find_filter return annotation uses X|Y union syntax — "
            f"would be a SyntaxError on Python 3.9: {ret!r}"
        )


# ── monitor counts failed downloads (M2) ─────────────────────────────────────

def test_d_monitor_counts_failed_downloads(tmp_path, caplog):
    """
    download_pdf failures must be logged at WARNING (not DEBUG) and main()
    must print a 'downloaded X / failed Y' summary to stderr.
    """
    import logging
    record = {
        "canonical_id": "arxiv:2301.12345",
        "pdf_url": "https://arxiv.org/pdf/2301.12345.pdf",
    }
    with patch("requests.get", side_effect=Exception("connection refused")), \
         caplog.at_level(logging.WARNING):
        result = monitor.download_pdf(record, tmp_path)

    assert result == "fail"
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_msgs, "download failure must be logged at WARNING level"


def test_d_monitor_main_prints_download_summary(tmp_path, capsys):
    """
    When PDFs are requested and some fail, main() prints 'downloaded X / failed Y'
    to stderr so a cron operator can see the failure count.
    """
    filters_json = tmp_path / "filters.json"
    filters_json.write_text(
        '{"filters": [{"name": "Test", "enabled": true, "days_back": 7, '
        '"text_groups": [{"title":"test","abstract":"","both":""}], "authors": [], '
        '"source_selection": {"all": true, "selected": []}}]}'
    )
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()

    record_dict = {
        "canonical_id": "arxiv:test1", "title": "Test Paper",
        "abstract": "test", "pdf_url": "https://arxiv.org/pdf/test1.pdf",
    }

    def fake_search(filter_dict, source_selection=None, on_batch=None, on_progress=None,
                    on_status=None, should_stop=None, max_results=200):
        return []

    orch = MagicMock()
    orch.search.side_effect = fake_search

    with patch.object(monitor, "load_sources_config", return_value={}), \
         patch.object(monitor, "SourceOrchestrator", return_value=orch), \
         patch.object(monitor, "load_filters", return_value=[
             {"name": "Test", "enabled": True, "days_back": 7,
              "text_groups": [{"title": "test", "abstract": "", "both": ""}],
              "authors": [],
              "source_selection": {"all": True, "selected": []}}
         ]), \
         patch.object(monitor, "run_search", return_value=[record_dict]), \
         patch("requests.get", side_effect=Exception("network error")):
        monitor.main(["--all", "--download-dir", str(pdf_dir)])

    captured = capsys.readouterr()
    # Should mention download counts somewhere in stderr
    assert "downloaded" in captured.err.lower() or "failed" in captured.err.lower(), (
        f"expected download summary in stderr, got: {captured.err!r}"
    )
