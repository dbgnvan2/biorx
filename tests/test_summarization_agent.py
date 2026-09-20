"""
Tests for the desktop summarization agent's choice of model.

Spec: docs/implementation_plan_2026-09-18_filter_run.md#M1 — the agent used a
hard-coded Ollama qwen:7b (not installed) and labelled every summary "qwen:7b"
whatever produced it. It now uses llm_config.yaml's default provider and records
the model that actually ran.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from agents import summarization_agent as agent_module
from agents.summarization_agent import SummarizationAgent
from src.llm_config import load_llm_config
from src.tokens import UNCOUNTED
from src.llm_providers import NoLLMCredentialError, ResolvedLLM, resolve_client


def _paper(db, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 stand-in")
    pid = db.insert_paper({"title": "Stress and inflammation", "doi": "10.1/x",
                           "abstract": "An abstract.", "canonical_id": "doi:10.1/x"})
    db.update_paper_path(pid, str(pdf))
    return pid


def test_m1_repo_default_is_deepseek(monkeypatch):
    """The shipped llm_config.yaml makes DeepSeek the default, and resolving it
    with an owner key yields the DeepSeek client and model (no network)."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    resolved = resolve_client(config=load_llm_config())
    assert (resolved.provider, resolved.model) == ("deepseek", "deepseek-flash")
    assert type(resolved.client).__name__ == "DeepSeekClient"


def test_m1_agent_uses_the_configured_default(tmp_path, monkeypatch):
    """The agent asks resolve_client (the config), not a hard-coded OllamaClient."""
    fake = MagicMock()
    fake.summarize_paper.return_value = ({"key_findings": ["a"], "methodology": "m",
                                          "conclusions": "c"}, UNCOUNTED)
    calls = []

    def fake_resolve(**kwargs):
        calls.append(kwargs)
        return ResolvedLLM(fake, "deepseek", "deepseek-flash", "owner")

    monkeypatch.setattr(agent_module, "resolve_client", fake_resolve)
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = _paper(agent.db, tmp_path)
    with patch.object(agent.pdf_handler, "extract_text", return_value="Full text."):
        assert agent.summarize_paper_by_id(pid) is True
    assert len(calls) == 1
    fake.summarize_paper.assert_called_once()


def test_m1_summary_records_the_model_that_ran(tmp_path, monkeypatch):
    """The stored summary names the model that wrote it — never an assumed qwen:7b."""
    fake = MagicMock()
    fake.summarize_paper.return_value = ({"key_findings": ["a"], "methodology": "m",
                                          "conclusions": "c"}, UNCOUNTED)
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(fake, "deepseek", "deepseek-flash", "owner"))
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = _paper(agent.db, tmp_path)
    with patch.object(agent.pdf_handler, "extract_text", return_value="Full text."):
        agent.summarize_paper_by_id(pid)
    assert agent.db.get_summary(pid)["model_version"] == "deepseek-flash"


def test_m1_missing_key_is_reported_not_crashed(tmp_path, monkeypatch):
    """No API key: the agent is still constructed (the GUI builds it at start-up)
    and a run reports why it could not summarize."""
    def no_key(**_):
        raise NoLLMCredentialError("no API key available for deepseek")

    monkeypatch.setattr(agent_module, "resolve_client", no_key)
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(agent.db, "get_unsummarized_papers",
                        lambda limit=10: [{"id": 1, "title": "x"}])
    result = agent.summarize_all_unsummarized()
    assert result["success"] is False
    assert "no API key" in result["error"]
    assert result["failed_count"] == 1


def test_m1_insert_summary_assumes_no_model(tmp_path):
    """A caller that does not say which model ran gets an empty label, not qwen:7b."""
    from src.db import Database
    db = Database(str(tmp_path / "t.db"))
    try:
        pid = db.insert_paper({"title": "T", "doi": "10.1/y", "canonical_id": "doi:10.1/y"})
        db.insert_summary(paper_id=pid, summary_text="s")
        assert db.get_summary(pid)["model_version"] == ""
    finally:
        db.close()


def test_r4_blank_summary_is_not_saved(tmp_path, monkeypatch):
    """Review finding 4: Ollama's parser returns empty fields for a reply in
    the wrong shape. The agent must fail it, not store a blank summary."""
    fake = MagicMock()
    fake.summarize_paper.return_value = ({"key_findings": [], "methodology": "",
                                         "conclusions": ""}, UNCOUNTED)
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(fake, "ollama", "qwen3.5:4b", "none"))
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = _paper(agent.db, tmp_path)
    with patch.object(agent.pdf_handler, "extract_text", return_value="Full text."):
        assert agent.summarize_paper_by_id(pid) is False
    assert agent.db.get_summary(pid) is None


def _tags(*names):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"models": [{"name": n} for n in names]}
    return resp


@pytest.mark.parametrize("model,installed,ok", [
    ("qwen3.5:4b", ["qwen3.5:4b", "nomic-embed-text:latest"], True),
    ("qwen:7b", ["qwen3.5:4b"], False),                  # the original problem
    ("llama3", ["llama3:latest"], True),                 # untagged means :latest
])
def test_r4_ollama_available_only_if_model_installed(model, installed, ok):
    from src.llm import OllamaClient
    with patch("src.llm.requests.get", return_value=_tags(*installed)):
        assert OllamaClient(model=model).is_available() is ok


def test_r4_ollama_gets_config_budget_and_timeout():
    """build_client passes llm_config's budget and timeout, and the prompt
    carries exactly max_chars of text and no stray code comment."""
    from src.llm_config import provider_config
    from src.llm_providers import build_client
    pconf = provider_config(load_llm_config(), "ollama")
    client = build_client(pconf, "", 50)
    assert (client.max_chars, client.timeout) == (50, pconf.timeout)
    sent = {}
    with patch.object(client, "generate",
                      side_effect=lambda p, **_: (sent.setdefault("p", p) and "", UNCOUNTED)):
        client.summarize_paper("abs", "x" * 80)
    assert "x" * 50 in sent["p"] and "x" * 51 not in sent["p"]
    assert "# Limit" not in sent["p"]


def test_r7_cli_names_the_paid_model_before_running(monkeypatch, capsys):
    """Review finding 7: the CLI says which model runs and that it is billed,
    before any paper is summarized."""
    fake = MagicMock()
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(fake, "deepseek", "deepseek-flash", "owner"))
    monkeypatch.setattr(sys, "argv", ["summarization_agent", "--max-count", "3"])
    monkeypatch.setattr(agent_module, "Database", MagicMock())
    with patch.object(SummarizationAgent, "summarize_all_unsummarized",
                      return_value={"success": True}) as run:
        assert agent_module.main() == 0
    err = capsys.readouterr().err
    assert "Summarizing up to 3 paper(s) with deepseek (deepseek-flash) — a paid API" in err
    run.assert_called_once()


def test_r7_cli_without_a_key_stops_before_running(monkeypatch, capsys):
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: (_ for _ in ()).throw(NoLLMCredentialError("no API key")))
    monkeypatch.setattr(sys, "argv", ["summarization_agent"])
    monkeypatch.setattr(agent_module, "Database", MagicMock())
    with patch.object(SummarizationAgent, "summarize_all_unsummarized") as run:
        assert agent_module.main() == 1
    assert "Cannot summarize: no API key" in capsys.readouterr().err
    run.assert_not_called()


def test_ft1_5_agent_uses_the_abstract_without_a_model_call(tmp_path, monkeypatch):
    """FT1.5: no downloaded PDF and no free copy online — the model is not
    called; the abstract is stored as the entry, marked source_text=abstract."""
    from src.fulltext import FullText
    fake = MagicMock()
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(fake, "deepseek", "deepseek-flash", "owner"))
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = agent.db.insert_paper({"title": "No PDF here", "doi": "10.1/np",
                                 "abstract": "What the abstract says.", "canonical_id": "doi:10.1/np"})
    with patch.object(agent, "_find_full_text", return_value=FullText(tried=["OpenAlex: no free copy"])):
        assert agent.summarize_paper_by_id(pid) is True
    fake.summarize_paper.assert_not_called()
    stored = agent.db.get_summary(pid)
    assert (stored["source_text"], stored["summary_text"], stored["model_version"]) == \
        ("abstract", "What the abstract says.", "")


def test_ft1_5_agent_summarizes_a_copy_found_online(tmp_path, monkeypatch):
    from src.fulltext import FullText
    fake = MagicMock()
    fake.summarize_paper.return_value = ({"key_findings": ["a"], "methodology": "m",
                                         "conclusions": "c"}, UNCOUNTED)
    monkeypatch.setattr(agent_module, "resolve_client",
                        lambda **_: ResolvedLLM(fake, "deepseek", "deepseek-flash", "owner"))
    agent = SummarizationAgent(db_path=str(tmp_path / "t.db"))
    pid = agent.db.insert_paper({"title": "Found online", "doi": "10.1/fo",
                                 "abstract": "Abs.", "canonical_id": "doi:10.1/fo"})
    with patch.object(agent, "_find_full_text",
                      return_value=FullText(text="The paper's full text.", source="OpenAlex")):
        assert agent.summarize_paper_by_id(pid) is True
    assert fake.summarize_paper.call_args.args[1] == "The paper's full text."
    stored = agent.db.get_summary(pid)
    assert (stored["source_text"], stored["text_source"]) == ("full_text", "OpenAlex")
