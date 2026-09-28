"""
The shared summarize pipeline (src/summarize.py) and the CLI agent built on it.

Spec: docs/implementation_plan_2026-09-28_review_fixes.md#S1, #A1, #M26, #A8,
      #M28, #A11, #A12, #M25, #A13
"""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.db import Database
from src.summarize import (PaperLookupError, lookup_at_source, summarize_paper)
from src.tokens import UNCOUNTED

SUMMARY = {"key_findings": ["f1"], "methodology": "m", "conclusions": "c"}


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _client(summary=SUMMARY):
    c = MagicMock()
    c.summarize_paper.return_value = (summary, UNCOUNTED)
    return c


def _paper(doi="10.1/p", abstract="An abstract."):
    return {"doi": doi, "canonical_id": f"doi:{doi}", "title": "Paper", "abstract": abstract}


def _text(text):
    return lambda _p: (text, "used" if text else "not found", "Unpaywall" if text else "")


def _row(db, doi):
    pid = db.find_paper({"doi": doi})["id"]
    s = db.get_summary(pid)
    return {k: s[k] for k in ("summary_text", "key_findings", "methodology",
                              "conclusions", "model_version", "source_text", "text_source")}


# ── S1: one pipeline, one stored shape ────────────────────────────────────────

def test_s1_route_and_cli_store_identical_rows(tmp_path, monkeypatch):
    """The web route and the CLI both go through summarize_paper, so a paper
    summarized by either is stored the same way."""
    from agents import summarization_agent as agent_module
    from src.llm_providers import ResolvedLLM

    web_db = Database(str(tmp_path / "web.db"))
    summarize_paper(web_db, _paper(), lambda: (_client(), "deepseek-flash"),
                    find_text=_text("Full text."))

    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(_client(), "deepseek", "deepseek-flash", "owner"))
    agent = agent_module.SummarizationAgent(db_path=str(tmp_path / "cli.db"))
    pid = agent.db.insert_paper(_paper())
    with patch.object(agent, "_find_text", _text("Full text.")):
        assert agent.summarize_paper_by_id(pid) is True

    assert _row(web_db, "10.1/p") == _row(agent.db, "10.1/p")
    assert _row(agent.db, "10.1/p")["summary_text"] == ""       # A13 convention
    web_db.close()


# ── M26: a paid summary is never replaced by an abstract ─────────────────────

def test_m26_existing_full_text_is_reused(db):
    summarize_paper(db, _paper(), lambda: (_client(), "m1"), find_text=_text("Full."))
    second = MagicMock()
    out = summarize_paper(db, _paper(), lambda: (second, "m2"), find_text=_text(""))
    assert out.reused and out.summary["key_findings"] == ["f1"]
    second.summarize_paper.assert_not_called()


def test_m26_cli_path_cannot_downgrade(tmp_path, monkeypatch):
    """--paper-id on a paper with a full-text summary, while the finder is down."""
    from agents.summarization_agent import SummarizationAgent
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = agent.db.insert_paper(_paper())
    agent.db.insert_summary(pid, summary_text="", key_findings=["paid"],
                            source_text="full_text", model_version="m1")
    with patch.object(agent, "_find_text", _text("")):
        assert agent.summarize_paper_by_id(pid) is True
    assert agent.db.get_summary(pid)["key_findings"] == ["paid"]


# ── A8: not saved is reported, never "done" ──────────────────────────────────

def test_a8_not_saved_is_reported(db):
    from src.db import SummaryNotSaved
    with patch.object(type(db), "insert_summary", side_effect=SummaryNotSaved("locked")):
        out = summarize_paper(db, _paper(), lambda: (_client(), "m"), find_text=_text("Full."))
    assert out.saved is False and "locked" in out.not_saved_reason


def test_a8_cli_reports_failure(tmp_path, monkeypatch):
    from agents import summarization_agent as agent_module
    from src.db import SummaryNotSaved
    from src.llm_providers import ResolvedLLM
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(_client(), "deepseek", "m", "owner"))
    agent = agent_module.SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = agent.db.insert_paper(_paper())
    with patch.object(agent, "_find_text", _text("Full.")), \
         patch.object(type(agent.db), "insert_summary", side_effect=SummaryNotSaved("locked")):
        assert agent.summarize_paper_by_id(pid) is False


# ── M28: a NULL abstract is "no abstract", not a crash ───────────────────────

def test_m28_null_abstract(db):
    from src.llm_providers import ProviderResponseError
    paper = _paper(abstract=None)
    recovered = SimpleNamespace(found=False, tried=["europepmc"], failed=[])
    with pytest.raises(ProviderResponseError, match="No abstract or downloadable text"):
        summarize_paper(db, paper, lambda: (_client(), "m"), find_text=_text(""),
                        recover=lambda _p: recovered)


# ── A11: the batch queue ──────────────────────────────────────────────────────

def test_a11_selects_undownloaded(db):
    pid = db.insert_paper(_paper())            # never downloaded (nothing sets it)
    assert [p["id"] for p in db.get_unsummarized_papers(10)] == [pid]


def test_a11_failing_papers_do_not_starve_others(tmp_path, monkeypatch):
    """Real scale for the cap: 3 x limit papers, a third of them always failing.
    Every paper must be tried within three runs."""
    from agents import summarization_agent as agent_module
    from src.llm_providers import ResolvedLLM
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(_client(), "deepseek", "m", "owner"))
    agent = agent_module.SummarizationAgent(db_path=str(tmp_path / "t.db"))
    limit = 5
    ids = [agent.db.insert_paper(_paper(f"10.1/{i}", abstract=None if i % 3 == 0 else "a"))
           for i in range(3 * limit)]
    tried = set()
    real = agent._summarize_paper

    def spy(paper):
        tried.add(paper["id"])
        return real(paper)

    agent._summarize_paper = spy
    with patch.object(agent, "_find_text", _text("")):
        for _ in range(3):
            agent.summarize_all_unsummarized(max_count=limit)
    assert tried == set(ids)


# ── A12 / M25: the CLI's database ─────────────────────────────────────────────

def test_a12_cli_honours_data_dir(tmp_path, monkeypatch):
    from agents.summarization_agent import SummarizationAgent
    monkeypatch.delenv("BIORX_DB_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    agent = SummarizationAgent()
    assert Path(agent.db.db_path).resolve() == (tmp_path / "biorxiv.db").resolve()


def test_m25_mock_refuses_default_db(monkeypatch, capsys):
    from agents import summarization_agent as agent_module
    monkeypatch.setattr(sys, "argv", ["summarization_agent.py", "--mock"])
    with patch.object(agent_module, "Database") as opened:
        assert agent_module.main() == 2
    opened.assert_not_called()
    assert "--db-path" in capsys.readouterr().err


# ── A1: looking a paper up at its source ──────────────────────────────────────

def test_a1_arxiv_paper_resolved_at_source():
    raw = {"arxiv_id_full": "2401.00001v1", "title": "Agents", "abstract": "a",
           "authors": ["Ann Lee"], "published": "2024-01-01"}
    with patch("src.sources.arxiv.ArxivAdapter.get_by_id", return_value=raw):
        paper = lookup_at_source({"doi": "", "canonical_id": "arxiv:2401.00001"})
    assert paper["title"] == "Agents" and paper["canonical_id"] == "arxiv:2401.00001"


def test_a1_doi_resolved_via_crossref():
    msg = {"title": ["From Crossref"], "abstract": "<jats:p>Abs</jats:p>",
           "author": [{"given": "A", "family": "Lee"}], "type": "posted-content"}
    with patch("src.sources.europepmc.EuropePmcAdapter.get_by_id", return_value=None), \
         patch("src.sources.crossref.CrossrefAdapter.get_by_id", return_value=msg):
        paper = lookup_at_source({"doi": "10.1/x", "canonical_id": ""})
    assert paper["title"] == "From Crossref" and paper["doi"] == "10.1/x"
    assert paper["is_preprint"] is True


def test_a1_unknown_doi_is_none():
    with patch("src.sources.europepmc.EuropePmcAdapter.get_by_id", return_value=None), \
         patch("src.sources.crossref.CrossrefAdapter.get_by_id", return_value=None):
        assert lookup_at_source({"doi": "10.1/none", "canonical_id": ""}) is None


def test_a1_source_outage_is_retryable():
    from src.sources.errors import SourceUnavailableError
    with patch("src.sources.europepmc.EuropePmcAdapter.get_by_id",
               side_effect=SourceUnavailableError("down")):
        with pytest.raises(PaperLookupError):
            lookup_at_source({"doi": "10.1/x", "canonical_id": ""})


# ── A13: CLI summaries are not sent twice to the review model ────────────────

def test_a13_migration_clears_duplicate_text(tmp_path):
    path = str(tmp_path / "t.db")
    d = Database(path)
    pid = d.insert_paper(_paper())
    d.conn.execute(
        "INSERT INTO summaries (paper_id, summary_text, key_findings, methodology, "
        "conclusions, source_text) VALUES (?, ?, ?, ?, ?, 'full_text')",
        (pid, "KEY FINDINGS:\n- f1\n\nMETHODOLOGY:\nm", '["f1"]', "m", "c"))
    d.conn.commit()
    d.close()
    d = Database(path)                           # migration runs on open
    assert d.get_summary(pid)["summary_text"] == ""
    d.close()
    d = Database(path)                           # and again: idempotent
    assert d.get_summary(pid)["key_findings"] == ["f1"]
    d.close()


def test_a1_pmcid_resolved_via_europe_pmc():
    """Gate 2026-09-28 batch 2, finding 3: pmcid: ids are looked up too."""
    raw = {"pmcid": "PMC77", "title": "Found by PMCID", "pubTypeList": {"pubType": ["Review"]}}
    with patch("src.sources.europepmc.EuropePmcAdapter.get_by_id", return_value=raw) as get:
        paper = lookup_at_source({"doi": "", "canonical_id": "pmcid:PMC77"})
    get.assert_called_once_with("PMC77")
    assert paper["title"] == "Found by PMCID"


def test_a1_europe_pmc_queries_pmcid_by_field():
    from src.sources.europepmc import EuropePmcAdapter
    a = EuropePmcAdapter()
    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = {"resultList": {"result": []}}
    a.session.get = MagicMock(return_value=resp)
    a.get_by_id("pmc77")
    assert a.session.get.call_args.kwargs["params"]["query"] == "PMCID:PMC77"


def test_a1_title_fingerprint_says_why_it_cannot_be_looked_up(db):
    from src.summarize import PaperNotFoundError, resolve_paper
    lookup = MagicMock()
    with pytest.raises(PaperNotFoundError, match="title fingerprint"):
        resolve_paper(db, {"doi": "", "canonical_id": "title:abc123"}, lookup=lookup)
    lookup.assert_not_called()
