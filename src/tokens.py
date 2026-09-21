"""
Token usage reported by an LLM provider.

Purpose: Carry what a provider says a call cost, without inventing what it does
         not say.
Spec:    docs/implementation_plan_2026-09-20_references_batch.md#M1.A.1
Tests:   tests/test_tokens.py

The distinction this module exists to preserve: a provider that reports no usage
is **not** a call that cost nothing. `counted=False` says "this ran, and nobody
told us what it cost"; a zero-token TokenUsage with `counted=True` would be a
false record, and a meter that adds it silently under-reports (learnings P2).

Each `from_*` reader is tolerant by design. A missing or malformed usage block
means the provider did not tell us, which is an uncounted call, not an error —
the summary itself is still good and must not be thrown away over its receipt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenUsage:
    """What one model call cost, as the provider reported it.

    prompt/completion/total are whole tokens. `counted` is False when the
    provider reported nothing usable — then the three numbers are 0 and mean
    "unknown", never "free".
    """

    prompt: int = 0
    completion: int = 0
    total: int = 0
    counted: bool = False

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Sum two usages. An uncounted call contributes nothing to the totals
        but is not allowed to make the sum look complete: the result is counted
        only if both sides were."""
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(
            prompt=self.prompt + other.prompt,
            completion=self.completion + other.completion,
            total=self.total + other.total,
            counted=self.counted and other.counted,
        )


UNCOUNTED = TokenUsage()
"""The provider said nothing about what this call cost."""


def _whole(value: Any) -> Optional[int]:
    """A non-negative int from a provider field, or None if it is not one.

    Providers have been known to send null, a string, or a float here. Anything
    that is not a sane count is treated as absent rather than coerced, so a
    junk value cannot silently become a token total.
    """
    if isinstance(value, bool):          # bool is an int subclass; not a count
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _build(prompt: Optional[int], completion: Optional[int],
           provider: str, total: Optional[int] = None) -> TokenUsage:
    """A TokenUsage from two counts, or UNCOUNTED when neither is usable."""
    if prompt is None and completion is None:
        logger.debug("%s reported no usable token usage", provider)
        return UNCOUNTED
    prompt = prompt or 0
    completion = completion or 0
    return TokenUsage(
        prompt=prompt,
        completion=completion,
        total=total if total is not None else prompt + completion,
        counted=True,
    )


def from_openai_style(data: Any, provider: str = "OpenAI-compatible") -> TokenUsage:
    """Usage from an OpenAI-dialect response body (DeepSeek, and any other
    `/chat/completions` provider — see standards L8).

    Shape: {"usage": {"prompt_tokens": N, "completion_tokens": N,
                      "total_tokens": N}}
    """
    if not isinstance(data, Mapping):
        return UNCOUNTED
    usage = data.get("usage")
    if not isinstance(usage, Mapping):
        logger.debug("%s response carried no usage block", provider)
        return UNCOUNTED
    return _build(
        _whole(usage.get("prompt_tokens")),
        _whole(usage.get("completion_tokens")),
        provider,
        total=_whole(usage.get("total_tokens")),
    )


def from_anthropic(response: Any) -> TokenUsage:
    """Usage from an Anthropic Messages API response object.

    `input_tokens` counts only the tokens that were **not** served from or
    written to the prompt cache; cached tokens are reported separately and are
    still tokens the call consumed. Summing all three is what makes the prompt
    figure comparable with other providers', and keeps the number honest if
    prompt caching is ever turned on here.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        logger.debug("Anthropic response carried no usage block")
        return UNCOUNTED

    parts = [
        _whole(getattr(usage, "input_tokens", None)),
        _whole(getattr(usage, "cache_creation_input_tokens", None)),
        _whole(getattr(usage, "cache_read_input_tokens", None)),
    ]
    prompt = None if all(p is None for p in parts) else sum(p or 0 for p in parts)
    return _build(prompt, _whole(getattr(usage, "output_tokens", None)), "Anthropic")


def from_ollama(data: Any) -> TokenUsage:
    """Usage from an Ollama `/api/generate` response body.

    Ollama names these differently from everyone else, and omits them entirely
    on some model/version combinations — those runs are uncounted, not free.
    """
    if not isinstance(data, Mapping):
        return UNCOUNTED
    return _build(
        _whole(data.get("prompt_eval_count")),
        _whole(data.get("eval_count")),
        "Ollama",
    )


# ── Estimating a batch before it runs (M5) ────────────────────────────────────
#
# These produce a RANGE, and every caller must present it as an estimate. The
# upper bound is not a guess: max_text_chars caps what is ever sent to the
# model, so a summary cannot exceed it however long the paper is. The lower
# bound is a short paper. What the estimate cannot know is which of the two a
# given paper turns out to be — hence a range rather than a figure.


def _settings(config: Mapping) -> Mapping:
    """The token_estimate block, or {} — callers fall back per key (P4)."""
    got = (config or {}).get("token_estimate")
    return got if isinstance(got, Mapping) else {}


def _setting(config: Mapping, key: str, fallback):
    value = _settings(config).get(key, fallback)
    return value if isinstance(value, (int, float)) and value >= 0 else fallback


def rate_for(config: Mapping, model: str) -> Optional[Mapping]:
    """USD per 1M tokens for a model, or None when none is configured.

    None means "no price is known", and the caller must show token counts
    without a dollar figure. Substituting another model's rate, or a zero,
    would put a number on screen that nobody can stand behind.
    """
    rates = _settings(config).get("rates")
    if not isinstance(rates, Mapping):
        return None
    entry = rates.get(model)
    if not isinstance(entry, Mapping):
        return None
    if not isinstance(entry.get("input"), (int, float)):
        return None
    if not isinstance(entry.get("output"), (int, float)):
        return None
    return entry


def _dollars(config: Mapping, model: str, prompt: int, completion: int) -> Optional[float]:
    rate = rate_for(config, model)
    if rate is None:
        return None
    return (prompt * rate["input"] + completion * rate["output"]) / 1_000_000


def estimate_summary_tokens(n_papers: int, config: Mapping,
                            model: str = "") -> Dict[str, Any]:
    """What summarizing `n_papers` is likely to cost (M5.A.1).

    Returns low/high token totals, and low/high dollars when the model has a
    configured rate. `exact` is False: this is an estimate and every caller
    must say so.
    """
    if n_papers <= 0:
        return {"papers": 0, "low": 0, "high": 0, "exact": False,
                "dollars_low": None, "dollars_high": None, "model": model}

    per_token = _setting(config, "chars_per_token", 4) or 4
    low_chars = _setting(config, "text_chars_low", 3000)
    high_chars = _setting(config, "max_text_chars", 0) or (config or {}).get(
        "max_text_chars", 12000)
    overhead = _setting(config, "prompt_overhead_tokens", 200)
    out_low = _setting(config, "completion_tokens_low", 150)
    out_high = _setting(config, "completion_tokens_high", 600)

    prompt_low = int(low_chars / per_token) + int(overhead)
    prompt_high = int(high_chars / per_token) + int(overhead)

    low = n_papers * (prompt_low + int(out_low))
    high = n_papers * (prompt_high + int(out_high))
    return {
        "papers": n_papers,
        "low": low,
        "high": high,
        "exact": False,
        "dollars_low": _dollars(config, model, n_papers * prompt_low,
                                n_papers * int(out_low)),
        "dollars_high": _dollars(config, model, n_papers * prompt_high,
                                 n_papers * int(out_high)),
        "model": model,
    }


def estimate_text_tokens(text: str, config: Mapping) -> int:
    """Tokens in text that is already in hand — no guessing about its length."""
    per_token = _setting(config, "chars_per_token", 4) or 4
    return int(len(text or "") / per_token)
