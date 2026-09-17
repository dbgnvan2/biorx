"""
Purpose: Pluggable LLM backends (DeepSeek, Anthropic) and the provider resolver.
Spec:    docs/implementation_plan_2026-09-15.md#2.4, W2.a, W2.b, W2.d
Tests:   tests/web/test_providers.py, tests/web/test_summary_parsing.py

OllamaClient stays in src/llm.py untouched; this module adds the hosted backends
and the precedence chain that picks between them.

Two deliberate choices:

1. **Structured output, not text parsing.** src/llm.py parses Qwen's reply by
   splitting on blank lines and matching "KEY FINDINGS:" prefixes. A hosted
   model will not reliably emit that shape, and the parser fails *quietly* —
   returning a dict of empty strings rather than raising, which would store a
   blank summary and call it success (learnings P19, standards L4). Both hosted
   clients therefore ask for JSON against an explicit schema, and an
   unparseable reply raises.

2. **Typed failures.** src/llm.py returns None for every failure. The web app
   has to tell a user *why* — "no key" and "the provider is down" need different
   answers — so failures here are exceptions, never sentinel values that could
   be rendered as content (learnings P14).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from .llm_config import (
    ProviderConfig, default_provider, load_llm_config, max_text_chars,
    provider_config,
)

logger = logging.getLogger(__name__)

# Retry policy for hosted providers (standards E5): transient only, with backoff.
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2
RETRYABLE_STATUS = (408, 409, 429, 500, 502, 503, 504)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "methodology": {"type": "string"},
        "conclusions": {"type": "string"},
    },
    "required": ["key_findings", "methodology", "conclusions"],
    "additionalProperties": False,
}

SUMMARY_INSTRUCTIONS = (
    "You are summarizing a research paper for a researcher's reading queue.\n"
    "Return JSON with exactly these keys:\n"
    '  "key_findings"  — a list of at most {max_findings} short strings\n'
    '  "methodology"   — one short paragraph on how the study was done\n'
    '  "conclusions"   — one short paragraph on what the authors conclude\n'
    "Base every statement on the supplied text. If the text does not say, write "
    "that it does not say rather than inferring."
)


# ── Errors ────────────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Base class for provider failures."""


class NoLLMCredentialError(LLMError):
    """No usable key: neither the user's nor the server owner's."""


class ProviderUnavailableError(LLMError):
    """The provider could not be reached, or kept failing."""


class ProviderResponseError(LLMError):
    """The provider replied, but not with a summary we can use."""


# ── Shared helpers ────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    """Trim paper text to the budget, announcing what was dropped (L7, P9)."""
    if len(text) <= limit:
        return text
    logger.info("Paper text truncated: %d of %d characters kept", limit, len(text))
    return text[:limit]


# The abstract's share of the budget. It comes from a request body, so it is
# user-controlled: leaving it unbounded lets one crafted request send an
# arbitrarily large prompt to a backend billed to the server owner.
ABSTRACT_BUDGET_FRACTION = 0.25
MIN_ABSTRACT_BUDGET = 2000


def _abstract_budget(limit: int) -> int:
    return max(MIN_ABSTRACT_BUDGET, int(limit * ABSTRACT_BUDGET_FRACTION))


def _build_summary_prompt(abstract: str, full_text: str, limit: int,
                          max_findings: int = 3) -> str:
    """Build the summarization prompt. Pure — no network, so it is testable (L9).

    Paper content is delimited from the instructions so the model treats it as
    data rather than as instructions (standards L5). Both halves are budgeted:
    the abstract is user-supplied and was previously unbounded.
    """
    abstract = _truncate(abstract or "", _abstract_budget(limit))
    body = _truncate(full_text or "", limit)
    return (
        SUMMARY_INSTRUCTIONS.format(max_findings=max_findings)
        + "\n\n<paper_abstract>\n" + abstract.strip()
        + "\n</paper_abstract>\n\n<paper_text>\n" + body.strip() + "\n</paper_text>"
    )


def _coerce_summary(payload: Any) -> Dict[str, Any]:
    """Validate a decoded JSON summary into the canonical shape.

    Raises ProviderResponseError rather than returning blanks: a summary whose
    fields are all empty must not be stored as a successful result.
    """
    if not isinstance(payload, dict):
        raise ProviderResponseError(f"expected a JSON object, got {type(payload).__name__}")

    findings = payload.get("key_findings")
    if isinstance(findings, str):
        findings = [findings]
    if not isinstance(findings, list):
        findings = []
    findings = [str(f).strip() for f in findings if str(f).strip()]

    methodology = str(payload.get("methodology") or "").strip()
    conclusions = str(payload.get("conclusions") or "").strip()

    if not findings and not methodology and not conclusions:
        raise ProviderResponseError("provider returned an empty summary")

    return {
        "key_findings": findings,
        "methodology": methodology,
        "conclusions": conclusions,
    }


def _extract_json(text: str) -> Any:
    """Decode a JSON object from a model reply.

    Structured output should make the whole reply valid JSON. Some providers
    still wrap it in a ```json fence, so that one case is handled explicitly
    rather than by a permissive "find any braces" search.
    """
    if not text or not text.strip():
        raise ProviderResponseError("provider returned an empty response")
    candidate = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    try:
        return json.loads(candidate)
    except ValueError as e:
        raise ProviderResponseError(
            f"provider did not return JSON: {candidate[:200]}"
        ) from e


def _sleep_backoff(attempt: int) -> None:
    import time
    time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))


# ── DeepSeek (OpenAI-compatible dialect) ──────────────────────────────────────

class DeepSeekClient:
    """OpenAI-compatible chat client. Dialect: Bearer auth, /chat/completions."""

    def __init__(self, api_key: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com", timeout: int = 120,
                 max_chars: int = 12000):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_chars = max_chars

    # The interface src/llm.py's OllamaClient exposes.
    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, context: Optional[str] = None,
                 json_schema: Optional[Dict[str, Any]] = None) -> str:
        if not self.api_key:
            raise NoLLMCredentialError("no DeepSeek API key")

        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = e
                logger.warning("DeepSeek request failed (attempt %d/%d): %s",
                               attempt, MAX_ATTEMPTS, e)
                if attempt < MAX_ATTEMPTS:
                    _sleep_backoff(attempt)
                    continue
                raise ProviderUnavailableError(f"DeepSeek unreachable: {e}") from e

            if resp.status_code in (401, 403):
                raise NoLLMCredentialError("DeepSeek rejected the API key")
            if resp.status_code in RETRYABLE_STATUS:
                logger.warning("DeepSeek returned %d (attempt %d/%d)",
                               resp.status_code, attempt, MAX_ATTEMPTS)
                if attempt < MAX_ATTEMPTS:
                    _sleep_backoff(attempt)
                    continue
                raise ProviderUnavailableError(f"DeepSeek returned {resp.status_code}")
            if not resp.ok:
                raise ProviderResponseError(
                    f"DeepSeek returned {resp.status_code}: {resp.text[:200]}"
                )

            try:
                data = resp.json()
            except ValueError as e:      # standards E2: never bare .json()
                raise ProviderResponseError(
                    f"DeepSeek returned non-JSON: {resp.text[:200]}"
                ) from e

            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise ProviderResponseError(
                    f"DeepSeek response had an unexpected shape: {str(data)[:200]}"
                ) from e

        raise ProviderUnavailableError(f"DeepSeek failed after {MAX_ATTEMPTS} attempts: {last_error}")

    def summarize_paper(self, abstract: str, full_text: str,
                        max_findings: int = 3) -> Dict[str, Any]:
        prompt = _build_summary_prompt(abstract, full_text, self.max_chars, max_findings)
        raw = self.generate(prompt, json_schema=SUMMARY_SCHEMA)
        return _coerce_summary(_extract_json(raw))


# ── Anthropic (Messages API, official SDK) ────────────────────────────────────

class AnthropicClient:
    """Anthropic Messages API client, via the official `anthropic` SDK."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5",
                 timeout: int = 120, max_chars: int = 12000,
                 max_tokens: int = 4096):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_chars = max_chars
        self.max_tokens = max_tokens

    def _client(self):
        if not self.api_key:
            raise NoLLMCredentialError("no Anthropic API key")
        try:
            import anthropic
        except ImportError as e:      # pragma: no cover - depends on the install
            raise ProviderUnavailableError(
                "the `anthropic` package is not installed"
            ) from e
        return anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, context: Optional[str] = None,
                 json_schema: Optional[Dict[str, Any]] = None) -> str:
        client = self._client()
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if context:
            kwargs["system"] = context
        if json_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": json_schema}
            }

        try:
            response = client.messages.create(**kwargs)
        except Exception as e:
            # The SDK's typed errors are mapped by name so this module does not
            # import them at module scope (the package may be absent locally).
            name = type(e).__name__
            if name in ("AuthenticationError", "PermissionDeniedError"):
                raise NoLLMCredentialError("Anthropic rejected the API key") from e
            if name in ("RateLimitError", "APIConnectionError", "APITimeoutError",
                        "InternalServerError"):
                raise ProviderUnavailableError(f"Anthropic unavailable: {e}") from e
            raise ProviderResponseError(f"Anthropic call failed: {e}") from e

        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderResponseError("Anthropic declined to answer this request")

        try:
            return next(b.text for b in response.content if b.type == "text")
        except StopIteration as e:
            raise ProviderResponseError("Anthropic returned no text block") from e

    def summarize_paper(self, abstract: str, full_text: str,
                        max_findings: int = 3) -> Dict[str, Any]:
        prompt = _build_summary_prompt(abstract, full_text, self.max_chars, max_findings)
        raw = self.generate(prompt, json_schema=SUMMARY_SCHEMA)
        return _coerce_summary(_extract_json(raw))


# ── The resolver ──────────────────────────────────────────────────────────────

@dataclass
class ResolvedLLM:
    """Which client will run, whose credential it uses, and what it costs."""
    client: Any
    provider: str
    model: str
    key_source: str          # "user" | "owner" | "none"

    @property
    def billed_to_owner(self) -> bool:
        return self.key_source == "owner"


def build_client(pconf: ProviderConfig, api_key: str, max_chars: int,
                 model_override: str = ""):
    """Construct the client for a provider's dialect.

    model_override, when non-empty, replaces the config-file model name so a
    user can select a specific variant (e.g. claude-haiku-4-5 vs claude-sonnet-5)
    without the server owner having to restart.
    """
    model = model_override.strip() or pconf.model
    if pconf.dialect == "anthropic":
        return AnthropicClient(api_key=api_key, model=model,
                               timeout=pconf.timeout, max_chars=max_chars)
    if pconf.dialect == "openai":
        return DeepSeekClient(api_key=api_key, model=model,
                              base_url=pconf.base_url, timeout=pconf.timeout,
                              max_chars=max_chars)
    if pconf.dialect == "ollama":
        from .llm import OllamaClient
        return OllamaClient(base_url=pconf.base_url, model=model)
    raise ProviderUnavailableError(f"unknown provider dialect: {pconf.dialect!r}")


def resolve_client(user_provider: str = "", user_key: str = "",
                   user_model: str = "",
                   config: Optional[Dict[str, Any]] = None) -> ResolvedLLM:
    """Pick the client for this request.

    Precedence (W2.d): the requesting user's own key → the server owner's key →
    NoLLMCredentialError telling them to add one. The user's key is passed in
    already decrypted; this function never touches the database, so it stays
    testable without one.

    user_model, when non-empty, overrides the config-file model for this user's
    key. Ignored when falling back to the owner's key (the owner chooses the
    model for owner-billed requests).
    """
    cfg = config if config is not None else load_llm_config()
    budget = max_text_chars(cfg)

    # 1. The user's own key, for the provider they chose.
    if user_key:
        name = user_provider or default_provider(cfg)
        pconf = provider_config(cfg, name)
        if pconf is None:
            raise NoLLMCredentialError(
                f"your key is set for {name!r}, which is not a configured provider"
            )
        effective_model = user_model.strip() or pconf.model
        return ResolvedLLM(build_client(pconf, user_key, budget, user_model), pconf.name,
                           effective_model, "user")

    # 2. The server owner's key for the default provider.
    name = default_provider(cfg)
    pconf = provider_config(cfg, name)
    if pconf is None:
        raise NoLLMCredentialError(f"provider {name!r} is not configured")

    if not pconf.needs_key:
        # Local Ollama: no credential involved, so nobody is billed.
        return ResolvedLLM(build_client(pconf, "", budget), pconf.name,
                           pconf.model, "none")

    owner_key = pconf.owner_key()
    if owner_key:
        return ResolvedLLM(build_client(pconf, owner_key, budget), pconf.name,
                           pconf.model, "owner")

    raise NoLLMCredentialError(
        f"no API key available for {pconf.name}. Add your own key in LLM settings, "
        f"or ask the server owner to set {pconf.api_key_env}."
    )
