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
from typing import Any, Mapping, Optional

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
