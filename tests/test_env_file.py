"""Tests for src/env_file.py (review finding 3, 2026-09-18)."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import env_file
from src.env_file import load_project_env


def test_r3_env_file_sets_missing_values(tmp_path, monkeypatch):
    monkeypatch.delenv("BIORX_TEST_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("BIORX_TEST_KEY=from-file\n")
    assert load_project_env(f) is True
    assert os.environ["BIORX_TEST_KEY"] == "from-file"
    monkeypatch.delenv("BIORX_TEST_KEY")


def test_r3_existing_environment_wins(tmp_path, monkeypatch):
    """A deploy's or a shell's value is never overridden by the file."""
    monkeypatch.setenv("BIORX_TEST_KEY", "from-shell")
    f = tmp_path / ".env"
    f.write_text("BIORX_TEST_KEY=from-file\n")
    load_project_env(f)
    assert os.environ["BIORX_TEST_KEY"] == "from-shell"


def test_r3_missing_file_is_not_an_error(tmp_path):
    assert load_project_env(tmp_path / "absent.env") is False


def test_r3_tests_never_see_the_real_env_file():
    """The conftest guard is active: the default path is not the project's .env."""
    assert env_file.PROJECT_ENV.name == "no-such.env"


def test_r3_monitor_entry_point_loads_env(monkeypatch):
    """monitor.main reads .env before anything needs a key."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "monitor_for_env", Path(__file__).parent.parent / "agents" / "monitor.py")
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    called = []
    monkeypatch.setattr(env_file, "load_project_env", lambda *a, **k: called.append(1))
    monitor.main([])            # no --filter/--all: prints help and returns
    assert called == [1]


def test_r3_summarization_cli_loads_env(monkeypatch):
    from agents import summarization_agent
    called = []
    monkeypatch.setattr(env_file, "load_project_env", lambda *a, **k: called.append(1))
    monkeypatch.setattr(sys, "argv", ["summarization_agent", "--mock"])
    with patch.object(summarization_agent, "SummarizationAgent") as agent_cls:
        agent_cls.return_value.summarize_all_unsummarized.return_value = {"success": True}
        summarization_agent.main()
    assert called == [1]


def test_r3_web_app_loads_env_before_building_its_context(monkeypatch):
    """Owner keys in .env reach a locally run web app (the default provider
    for every user without a key of their own)."""
    from web import app as web_app
    order = []
    monkeypatch.setattr(env_file, "load_project_env", lambda *a, **k: order.append("env"))
    monkeypatch.setattr(web_app, "build_context", lambda: order.append("ctx") or MagicMock())
    web_app.create_app()
    assert order == ["env", "ctx"]


def test_r3_web_app_with_a_given_context_does_not_read_env(monkeypatch):
    from web import app as web_app
    called = []
    monkeypatch.setattr(env_file, "load_project_env", lambda *a, **k: called.append(1))
    web_app.create_app(MagicMock())
    assert called == []


def test_r3_env_load_logs_names_never_values(tmp_path, monkeypatch, caplog):
    """The start-up log says which names came from .env and which the
    environment already had — and never prints a value."""
    import logging
    monkeypatch.delenv("BIORX_A", raising=False)
    monkeypatch.setenv("BIORX_B", "shell")
    f = tmp_path / ".env"
    f.write_text("BIORX_A=secret-a\nBIORX_B=secret-b\n")
    with caplog.at_level(logging.INFO, logger="src.env_file"):
        load_project_env(f)
    text = caplog.text
    assert "BIORX_A" in text and "BIORX_B" in text
    assert "secret-a" not in text and "secret-b" not in text
    assert os.environ["BIORX_B"] == "shell"
    monkeypatch.delenv("BIORX_A")


def test_r3_provider_source_names_the_override(monkeypatch):
    from src.llm_config import default_provider_source
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert "LLM_PROVIDER environment variable" in default_provider_source({})
    monkeypatch.delenv("LLM_PROVIDER")
    assert "llm_config.yaml" in default_provider_source({})


def test_r3_blank_values_are_named(tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.delenv("BIORX_EMPTY", raising=False)
    f = tmp_path / ".env"
    f.write_text("BIORX_EMPTY=\n")
    with caplog.at_level(logging.WARNING, logger="src.env_file"):
        load_project_env(f)
    assert "Set but empty" in caplog.text and "BIORX_EMPTY" in caplog.text
    monkeypatch.delenv("BIORX_EMPTY", raising=False)
