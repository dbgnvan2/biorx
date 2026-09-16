"""
Tests for summary output handling across providers.

Spec: docs/implementation_plan_2026-09-15.md#2.4
This is the contract most likely to drift (learnings P19): src/llm.py parses
Qwen's prose by splitting on blank lines and matching "KEY FINDINGS:" prefixes,
and a hosted model will not reliably emit that. The hosted clients therefore ask
for JSON and validate it, and an unusable reply must raise rather than store a
blank summary that reads as success.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src.llm_providers import (
    AnthropicClient, DeepSeekClient, ProviderResponseError, _build_summary_prompt,
    _coerce_summary, _extract_json,
)

CANONICAL = {
    "key_findings": ["A", "B"],
    "methodology": "Method text.",
    "conclusions": "Conclusion text.",
}


# ── Both providers land on the same shape ─────────────────────────────────────

def _deepseek_returning(content):
    resp = MagicMock(status_code=200, ok=True, text=content)
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return patch("src.llm_providers.requests.post", return_value=resp)


def _anthropic_returning(content):
    block = MagicMock(); block.type = "text"; block.text = content
    msg = MagicMock(); msg.content = [block]; msg.stop_reason = "end_turn"
    sdk = MagicMock(); sdk.messages.create.return_value = msg
    return patch.object(AnthropicClient, "_client", return_value=sdk)


def test_each_provider_response_shape_parses_to_the_same_fields():
    body = json.dumps(CANONICAL)
    with _deepseek_returning(body):
        ds = DeepSeekClient(api_key="k").summarize_paper("a", "t")
    with _anthropic_returning(body):
        an = AnthropicClient(api_key="k").summarize_paper("a", "t")
    assert ds == an == CANONICAL


def test_a_json_fenced_reply_is_accepted():
    """Some providers wrap structured output in a markdown fence."""
    with _deepseek_returning("```json\n" + json.dumps(CANONICAL) + "\n```"):
        assert DeepSeekClient(api_key="k").summarize_paper("a", "t") == CANONICAL


# ── Unusable replies raise; they are never stored as content ──────────────────

@pytest.mark.parametrize("reply", [
    "",
    "   ",
    "I'm sorry, I can't help with that.",
    "KEY FINDINGS:\n- one\n\nMETHODOLOGY:\nprose",   # the Qwen shape, not JSON
])
def test_unparseable_response_is_failed_to_parse_not_a_blank_summary(reply):
    with _deepseek_returning(reply):
        with pytest.raises(ProviderResponseError):
            DeepSeekClient(api_key="k").summarize_paper("a", "t")


def test_an_all_empty_json_summary_is_refused():
    """
    Adversarial: well-formed JSON whose every field is empty would otherwise be
    stored as a successful summary and shown to the user as a blank card.
    """
    empty = json.dumps({"key_findings": [], "methodology": "", "conclusions": ""})
    with _deepseek_returning(empty):
        with pytest.raises(ProviderResponseError):
            DeepSeekClient(api_key="k").summarize_paper("a", "t")


def test_a_json_array_is_refused():
    with _deepseek_returning('["not", "an", "object"]'):
        with pytest.raises(ProviderResponseError):
            DeepSeekClient(api_key="k").summarize_paper("a", "t")


# ── Coercion details ──────────────────────────────────────────────────────────

def test_a_single_string_finding_is_accepted_as_a_list():
    out = _coerce_summary({"key_findings": "only one", "methodology": "m",
                           "conclusions": "c"})
    assert out["key_findings"] == ["only one"]


def test_blank_findings_are_dropped_but_a_partial_summary_survives():
    out = _coerce_summary({"key_findings": ["real", "  ", ""], "methodology": "",
                           "conclusions": "c"})
    assert out["key_findings"] == ["real"]
    assert out["conclusions"] == "c"


def test_extract_json_reports_what_it_got():
    with pytest.raises(ProviderResponseError) as e:
        _extract_json("Sorry, no.")
    assert "Sorry, no." in str(e.value)


# ── Prompt building is pure and announces truncation (L5, L7, L9) ─────────────

def test_prompt_delimits_paper_content_from_instructions():
    prompt = _build_summary_prompt("ABS", "BODY", limit=1000)
    assert "<paper_abstract>\nABS" in prompt
    assert "<paper_text>\nBODY" in prompt
    # The instruction layer comes first and is not interpolated with content.
    assert prompt.index("key_findings") < prompt.index("<paper_abstract>")


def test_prompt_truncates_to_the_budget_and_says_so(caplog):
    long_text = "x" * 5000
    with caplog.at_level("INFO"):
        prompt = _build_summary_prompt("a", long_text, limit=100)
    assert "x" * 101 not in prompt
    assert any("100 of 5000" in r.getMessage() for r in caplog.records)


def test_prompt_does_not_truncate_when_under_budget(caplog):
    with caplog.at_level("INFO"):
        prompt = _build_summary_prompt("a", "short", limit=1000)
    assert "short" in prompt
    assert not any("truncated" in r.getMessage() for r in caplog.records)


# ── Both halves of the prompt are budgeted ────────────────────────────────────

def test_a_huge_user_supplied_abstract_is_capped(caplog):
    """
    The abstract arrives in the request body, so it is user-controlled. Leaving
    it unbounded lets one crafted request send an arbitrarily large prompt to a
    backend billed to the server owner.
    """
    from src.llm_providers import _abstract_budget

    huge = "a" * 500_000
    with caplog.at_level("INFO"):
        prompt = _build_summary_prompt(huge, "body", limit=12000)

    budget = _abstract_budget(12000)
    assert len(prompt) < budget + 5000
    assert "a" * (budget + 1) not in prompt
    assert any("truncated" in r.getMessage() for r in caplog.records)


def test_a_normal_abstract_is_untouched():
    abstract = "A study of interactive simulacra across 25 agents."
    prompt = _build_summary_prompt(abstract, "body", limit=12000)
    assert abstract in prompt
