"""
Tests for src/llm_providers.py — the two hosted clients and the resolver.

Spec: docs/implementation_plan_2026-09-15.md#2.4, W2.a, W2.b, W2.c, W2.d
Every HTTP/SDK call is mocked. Live provider calls are integration-only and are
NOT covered by this suite (standards L9) — see README's manual smoke checklist.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import llm_providers as lp
from src.llm_config import load_llm_config, provider_config
from src.llm_providers import (
    AnthropicClient, DeepSeekClient, NoLLMCredentialError,
    ProviderResponseError, ProviderUnavailableError, resolve_client,
)

SUMMARY_JSON = {
    "key_findings": ["Agents cooperate", "Fidelity drops with scale"],
    "methodology": "Agent-based simulation across 200 runs.",
    "conclusions": "Scale degrades algorithmic fidelity.",
}


@pytest.fixture(autouse=True)
def _no_sleep():
    """Backoff must exist, but tests must not actually wait for it."""
    with patch("src.llm_providers._sleep_backoff"):
        yield


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                "ANTHROPIC_MODEL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(var, raising=False)


def _ds_response(content, status=200, ok=True):
    m = MagicMock()
    m.status_code = status
    m.ok = ok
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    m.text = content
    return m


# ── DeepSeek: OpenAI dialect (W2.a, standards L8) ─────────────────────────────

def test_deepseek_uses_openai_dialect_and_bearer_auth():
    client = DeepSeekClient(api_key="sk-ds-test", base_url="https://api.deepseek.com")
    with patch("src.llm_providers.requests.post",
               return_value=_ds_response(json.dumps(SUMMARY_JSON))) as post:
        client.summarize_paper("abstract", "text")

    url = post.call_args.args[0]
    headers = post.call_args.kwargs["headers"]
    body = post.call_args.kwargs["json"]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-ds-test"
    assert "x-api-key" not in headers          # that is the Anthropic dialect
    assert body["model"] == "deepseek-flash"
    assert "thinking" not in body              # nothing sent unless configured
    assert body["messages"][-1]["role"] == "user"


def test_deepseek_sets_a_timeout():
    client = DeepSeekClient(api_key="k", timeout=99)
    with patch("src.llm_providers.requests.post",
               return_value=_ds_response(json.dumps(SUMMARY_JSON))) as post:
        client.summarize_paper("a", "t")
    assert post.call_args.kwargs["timeout"] == 99


def test_deepseek_retries_a_retryable_status_then_succeeds():
    client = DeepSeekClient(api_key="k")
    responses = [_ds_response("", status=503, ok=False),
                 _ds_response(json.dumps(SUMMARY_JSON))]
    with patch("src.llm_providers.requests.post", side_effect=responses) as post:
        out = client.summarize_paper("a", "t")
    assert post.call_count == 2
    assert out["conclusions"] == SUMMARY_JSON["conclusions"]


def test_deepseek_gives_up_after_max_attempts():
    client = DeepSeekClient(api_key="k")
    with patch("src.llm_providers.requests.post",
               return_value=_ds_response("", status=503, ok=False)) as post:
        with pytest.raises(ProviderUnavailableError):
            client.summarize_paper("a", "t")
    assert post.call_count == lp.MAX_ATTEMPTS


def test_deepseek_does_not_retry_an_auth_failure():
    """401 is our bug, not theirs — retrying wastes time and confuses the user."""
    client = DeepSeekClient(api_key="bad")
    with patch("src.llm_providers.requests.post",
               return_value=_ds_response("", status=401, ok=False)) as post:
        with pytest.raises(NoLLMCredentialError):
            client.summarize_paper("a", "t")
    assert post.call_count == 1


def test_deepseek_guards_a_non_json_body():
    """standards E2: never call .json() unguarded."""
    client = DeepSeekClient(api_key="k")
    bad = MagicMock(status_code=200, ok=True, text="<html>gateway</html>")
    bad.json.side_effect = ValueError("no json")
    with patch("src.llm_providers.requests.post", return_value=bad):
        with pytest.raises(ProviderResponseError):
            client.summarize_paper("a", "t")


def test_deepseek_guards_an_unexpected_response_shape():
    client = DeepSeekClient(api_key="k")
    weird = MagicMock(status_code=200, ok=True, text="{}")
    weird.json.return_value = {"unexpected": True}
    with patch("src.llm_providers.requests.post", return_value=weird):
        with pytest.raises(ProviderResponseError):
            client.summarize_paper("a", "t")


def test_deepseek_without_a_key_raises_before_any_request():
    with patch("src.llm_providers.requests.post") as post:
        with pytest.raises(NoLLMCredentialError):
            DeepSeekClient(api_key="").summarize_paper("a", "t")
    post.assert_not_called()


# ── Anthropic: Messages API via the official SDK (W2.b) ───────────────────────

def _anthropic_message(text, stop_reason="end_turn"):
    block = MagicMock()
    block.type = "text"
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    msg.stop_reason = stop_reason
    return msg


def _patched_anthropic(message):
    """Patch the SDK client the module builds lazily."""
    sdk_client = MagicMock()
    sdk_client.messages.create.return_value = message
    return patch.object(AnthropicClient, "_client", return_value=sdk_client), sdk_client


def test_anthropic_uses_messages_api_with_configured_model():
    client = AnthropicClient(api_key="sk-ant-test", model="claude-sonnet-5")
    ctx, sdk = _patched_anthropic(_anthropic_message(json.dumps(SUMMARY_JSON)))
    with ctx:
        out = client.summarize_paper("abstract", "text")

    kwargs = sdk.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert out["key_findings"] == SUMMARY_JSON["key_findings"]


def test_default_anthropic_model_is_a_current_id():
    """
    Expected value from the current Anthropic model list (2026-09-15), not from
    the code: ids are claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5, with
    no date suffix. `claude-sonnet-4` — which the original spec asked for — is
    not a real model and would 404 on the first call.
    """
    current_ids = {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5",
                   "claude-opus-4-8", "claude-fable-5-1"}
    pconf = provider_config(load_llm_config(), "anthropic")
    assert pconf.model in current_ids
    assert pconf.model != "claude-sonnet-4"


def test_anthropic_model_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5")
    assert provider_config(load_llm_config(), "anthropic").model == "claude-opus-5"


def test_anthropic_maps_auth_errors_to_no_credential():
    class AuthenticationError(Exception):
        pass

    client = AnthropicClient(api_key="bad")
    sdk = MagicMock()
    sdk.messages.create.side_effect = AuthenticationError("bad key")
    with patch.object(AnthropicClient, "_client", return_value=sdk):
        with pytest.raises(NoLLMCredentialError):
            client.summarize_paper("a", "t")


def test_anthropic_maps_transient_errors_to_unavailable():
    class RateLimitError(Exception):
        pass

    client = AnthropicClient(api_key="k")
    sdk = MagicMock()
    sdk.messages.create.side_effect = RateLimitError("slow down")
    with patch.object(AnthropicClient, "_client", return_value=sdk):
        with pytest.raises(ProviderUnavailableError):
            client.summarize_paper("a", "t")


def test_anthropic_surfaces_a_refusal_rather_than_storing_it():
    client = AnthropicClient(api_key="k")
    ctx, _ = _patched_anthropic(_anthropic_message("", stop_reason="refusal"))
    with ctx:
        with pytest.raises(ProviderResponseError):
            client.summarize_paper("a", "t")


def test_anthropic_without_a_key_raises_before_building_a_client():
    with pytest.raises(NoLLMCredentialError):
        AnthropicClient(api_key="").summarize_paper("a", "t")


# ── Ollama stays as it was (W2.c) ─────────────────────────────────────────────

def test_ollama_client_interface_unchanged():
    from src.llm import OllamaClient
    c = OllamaClient()
    assert c.base_url == "http://localhost:11434"
    assert c.model == "qwen:7b"
    assert hasattr(c, "is_available") and hasattr(c, "generate")
    assert hasattr(c, "summarize_paper")


# ── The resolver: user key → owner key → error (W2.d) ─────────────────────────

def test_resolver_prefers_user_key_over_owner_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")

    resolved = resolve_client(user_provider="deepseek", user_key="sk-user")

    assert resolved.key_source == "user"
    assert resolved.provider == "deepseek"
    assert resolved.client.api_key == "sk-user"
    assert resolved.billed_to_owner is False


def test_resolver_falls_back_to_owner_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")

    resolved = resolve_client()

    assert resolved.key_source == "owner"
    assert resolved.provider == "anthropic"
    assert resolved.client.api_key == "sk-owner"
    assert resolved.billed_to_owner is True


def test_resolver_raises_typed_error_when_no_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(NoLLMCredentialError) as excinfo:
        resolve_client()
    message = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in message      # tells the operator what to set
    assert "LLM settings" in message           # tells the user what to do


def test_resolver_needs_no_key_for_local_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    resolved = resolve_client()
    assert resolved.key_source == "none"
    assert resolved.billed_to_owner is False


def test_resolver_rejects_an_unknown_provider_for_a_user_key():
    with pytest.raises(NoLLMCredentialError):
        resolve_client(user_provider="not-a-provider", user_key="sk-user")


def test_unknown_default_provider_falls_back_loudly(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    with caplog.at_level("ERROR"):
        resolved = resolve_client()
    assert resolved.provider == "ollama"
    assert any("mistral" in r.getMessage() for r in caplog.records)


# ── A key must never reach a log line (W3.c / D3) ─────────────────────────────

def test_key_never_appears_in_logs(caplog, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SUPERSECRETVALUE")
    with caplog.at_level("DEBUG"):
        resolved = resolve_client()
        client = DeepSeekClient(api_key="sk-ds-ALSOSECRET")
        with patch("src.llm_providers.requests.post",
                   return_value=_ds_response("", status=503, ok=False)):
            with pytest.raises(ProviderUnavailableError):
                client.summarize_paper("a", "t")

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "SUPERSECRETVALUE" not in logged
    assert "ALSOSECRET" not in logged
    assert resolved.key_source == "owner"



# ── DeepSeek thinking is off for summaries by config (2026-09-18) ────────────

def test_deepseek_thinking_setting_is_sent_when_configured():
    client = DeepSeekClient(api_key="k", thinking="disabled")
    with patch("src.llm_providers.requests.post",
               return_value=_ds_response(json.dumps(SUMMARY_JSON))) as post:
        client.summarize_paper("abstract", "text")
    assert post.call_args.kwargs["json"]["thinking"] == {"type": "disabled"}


def test_repo_config_uses_deepseek_flash_with_thinking_off():
    from src.llm_config import load_llm_config, provider_config
    from src.llm_providers import build_client
    raw = load_llm_config()["providers"]["deepseek"]
    assert raw["model"] == "deepseek-flash"           # the file, before any env override
    pconf = provider_config(load_llm_config(), "deepseek")
    assert pconf.thinking == "disabled"
    client = build_client(pconf, "k", 1000)
    assert client.thinking == "disabled"
