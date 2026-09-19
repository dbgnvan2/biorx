"""
Tests for src/llm_config.py — provider dialects, model ids, budgets, caps.

Spec: docs/implementation_plan_2026-09-15.md#2.1
Standards L1 (model ids in config, not source) and L8 (dialect from config, not
inferred from the model name).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import llm_config as lc
from src.llm_config import (
    default_provider, load_llm_config, max_text_chars, provider_config,
    summary_daily_cap,
)

REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("LLM_PROVIDER", "ANTHROPIC_MODEL", "DEEPSEEK_MODEL",
                "BIORX_LLM_CONFIG", "SUMMARY_DAILY_CAP_PER_USER",
                "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_the_real_config_file_defines_every_provider_the_code_uses():
    """The shipped llm_config.yaml is the contract, not a fixture."""
    cfg = load_llm_config()
    for name in ("ollama", "deepseek", "anthropic"):
        assert provider_config(cfg, name) is not None, f"{name} missing from config"


def test_dialects_come_from_config_not_from_the_model_name():
    cfg = load_llm_config()
    assert provider_config(cfg, "anthropic").dialect == "anthropic"
    assert provider_config(cfg, "deepseek").dialect == "openai"
    assert provider_config(cfg, "ollama").dialect == "ollama"


@pytest.mark.parametrize("provider,replacement", [
    ("anthropic", "claude-opus-5"),
    ("deepseek", "deepseek-reasoner"),
])
def test_the_client_takes_its_model_from_config_not_its_own_default(
    tmp_path, monkeypatch, provider, replacement
):
    """
    Standards L1, asserted behaviourally rather than by grepping the source for
    model literals: change the configured id and the constructed client must
    carry the new one. A class default that quietly won would fail here.
    """
    from src.llm_providers import build_client

    cfg = load_llm_config()
    original = provider_config(cfg, provider)
    assert original.model != replacement, "pick a replacement that differs"

    cfg["providers"][provider] = {**cfg["providers"][provider], "model": replacement}
    changed = provider_config(cfg, provider)
    client = build_client(changed, "sk-test", max_text_chars(cfg))

    assert client.model == replacement


def test_missing_config_file_falls_back_without_crashing(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        cfg = load_llm_config(str(tmp_path / "absent.yaml"))
    assert provider_config(cfg, "ollama") is not None
    assert any("not found" in r.getMessage() for r in caplog.records)


def test_a_malformed_config_file_falls_back_loudly(tmp_path, caplog):
    bad = tmp_path / "bad.yaml"
    bad.write_text("providers: [this is not a mapping\n")
    with caplog.at_level("ERROR"):
        cfg = load_llm_config(str(bad))
    assert provider_config(cfg, "ollama") is not None
    assert any("Could not read" in r.getMessage() for r in caplog.records)


def test_a_partial_config_does_not_delete_known_providers(tmp_path):
    partial = tmp_path / "partial.yaml"
    partial.write_text("providers:\n  deepseek:\n    dialect: openai\n    model: x\n")
    cfg = load_llm_config(str(partial))
    assert provider_config(cfg, "ollama") is not None    # still there
    assert provider_config(cfg, "deepseek").model == "x"


def test_env_overrides_the_default_provider(monkeypatch):
    cfg = load_llm_config()
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert default_provider(cfg) == "deepseek"
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert default_provider(cfg) == "ollama"


def test_env_overrides_a_model_id(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    assert provider_config(load_llm_config(), "deepseek").model == "deepseek-reasoner"


def test_owner_key_is_read_from_the_named_env_var(monkeypatch):
    pconf = provider_config(load_llm_config(), "anthropic")
    assert pconf.needs_key is True
    assert pconf.owner_key() == ""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-owner  ")
    assert pconf.owner_key() == "sk-owner"      # whitespace from a .env is stripped


def test_local_ollama_needs_no_key():
    assert provider_config(load_llm_config(), "ollama").needs_key is False


def test_unknown_provider_returns_none():
    assert provider_config(load_llm_config(), "nope") is None


def test_text_budget_and_cap_are_configurable(monkeypatch):
    cfg = load_llm_config()
    assert max_text_chars(cfg) > 0
    assert summary_daily_cap(cfg) == 25
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "5")
    assert summary_daily_cap(cfg) == 5
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "not-a-number")
    assert summary_daily_cap(cfg) == 25


def test_config_path_is_overridable(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("BIORX_LLM_CONFIG", str(target))
    assert lc.config_path() == target


# ── K2: malformed owner keys are reported, never printed (2026-09-18) ────────

def _cfg():
    return {"providers": {"anthropic": {"dialect": "anthropic", "model": "m",
                                        "api_key_env": "ANTHROPIC_API_KEY"},
                          "ollama": {"dialect": "ollama", "model": "q"}}}


def test_k2_doubled_key_is_reported_without_the_key(monkeypatch):
    from src.llm_config import owner_key_problems
    key = "sk-ant-api03-" + "A" * 40
    monkeypatch.setenv("ANTHROPIC_API_KEY", key + key)
    problems = owner_key_problems(_cfg())
    assert len(problems) == 1 and "pasted twice" in problems[0]
    assert key not in problems[0] and key[:10] not in problems[0]


def test_k2_key_with_quotes_or_spaces_is_reported(monkeypatch):
    from src.llm_config import owner_key_problems
    monkeypatch.setenv("ANTHROPIC_API_KEY", '"sk-ant-api03-abcdefghijklmnop"')
    assert "spaces or quotes" in owner_key_problems(_cfg())[0]


def test_k2_good_key_and_keyless_providers_are_quiet(monkeypatch):
    from src.llm_config import owner_key_problems
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "Ab1" * 30)
    assert owner_key_problems(_cfg()) == []


def test_k2_problem_reaches_healthz(ctx, client, monkeypatch):
    key = "sk-ant-api03-" + "B" * 40
    monkeypatch.setenv("ANTHROPIC_API_KEY", key + key)
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()
    ctx.orchestrator = None
    warnings = client.get("/healthz").json()["startup_warnings"]
    assert any("pasted twice" in w for w in warnings)
    assert not any(key[:12] in w for w in warnings)
