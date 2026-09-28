"""
Ollama/Qwen interface for summarization.
"""

import requests
import json
from typing import Optional, Dict, List, Any, Tuple
import logging

from .tokens import UNCOUNTED, TokenUsage, from_ollama

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
# Only for a bare OllamaClient(); every real path passes the model named in
# llm_config.yaml. Was "qwen:7b", which was never installed here (2026-09-18).
OLLAMA_MODEL = "qwen3.5:4b"


class OllamaClient:
    """Client for Ollama API."""

    def __init__(self, base_url: str = OLLAMA_URL, model: str = OLLAMA_MODEL,
                 timeout: int = 120, max_chars: int = 3000):
        """
        Initialize Ollama client.

        Args:
            base_url: Ollama API base URL
            model: Model name, as `ollama list` shows it (e.g. 'qwen3.5:4b')
            timeout: Seconds to wait for a generation (llm_config.yaml timeout)
            max_chars: Paper text sent per summary (llm_config.yaml max_text_chars)
        """
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_chars = max_chars

    def is_available(self) -> bool:
        """
        Check that Ollama is running AND this model is installed.

        Only checking the server let a configured model that was never pulled
        (qwen:7b) look available until the first summary failed (P6).
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code != 200:
                return False
            names = {m.get("name", "") for m in (response.json() or {}).get("models", [])}
        except (requests.RequestException, ValueError):
            logger.warning("Ollama is not available. Ensure it's running at %s", self.base_url)
            return False
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in names and self.model not in names:
            logger.warning("Ollama is running but model %r is not installed "
                           "(installed: %s). Run: ollama pull %s",
                           self.model, ", ".join(sorted(names)) or "none", self.model)
            return False
        return True

    def generate(self, prompt: str, context: Optional[str] = None,
                 json_schema: Optional[Dict[str, Any]] = None) -> Tuple[str, TokenUsage]:
        """
        Generate text using Ollama.

        Purpose: The same contract as the hosted clients: a reply or a typed error.
        Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A10, #M29, #M33
        Tests:   tests/test_tokens.py::test_m29_ollama_retried_then_unavailable,
                 tests/test_tokens.py::test_m1a1_ollama_client_returns_text_and_usage

        Args:
            prompt: Instruction prompt
            context: Optional system text
            json_schema: When given, Ollama is asked for a JSON reply.

        Returns:
            (generated text, what the call cost). Ollama does not always report
            counts, so the usage may be uncounted even on success — uncounted
            means unknown, not free (M1.A.1).

        Raises:
            ProviderUnavailableError after MAX_ATTEMPTS failed requests (a model
            still loading, a timeout, a 5xx); ProviderResponseError for a reply
            that is not JSON. It returned None before, which every caller had
            to remember to check (review A10/M29).
        """
        from .llm_providers import (MAX_ATTEMPTS, RETRYABLE_STATUS, ProviderResponseError,
                                    ProviderUnavailableError, _sleep_backoff)
        payload: Dict[str, Any] = {"model": self.model, "prompt": prompt, "stream": False}
        if context:
            payload["system"] = context
        if json_schema is not None:
            payload["format"] = "json"

        url = f"{self.base_url}/api/generate"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                # The same retryable set as the hosted clients: a 429 from a
                # busy local Ollama is "not right now", not a bad reply (gate
                # 2026-09-28 batch 2, finding 1).
                if status is not None and status not in RETRYABLE_STATUS:
                    raise ProviderResponseError(f"Ollama returned {status}: {e}") from e
                logger.warning("Ollama request failed (attempt %d/%d): %s",
                               attempt, MAX_ATTEMPTS, e)
                if attempt < MAX_ATTEMPTS:
                    _sleep_backoff(attempt)
                    continue
                raise ProviderUnavailableError(f"Ollama unreachable: {e}") from e
            try:
                result = response.json()
            except ValueError as e:
                raise ProviderResponseError(f"Ollama returned non-JSON: {e}") from e
            return (result.get("response") or "").strip(), from_ollama(result)
        raise ProviderUnavailableError("Ollama failed")   # pragma: no cover

    def summarize_paper(
        self,
        abstract: str,
        full_text: str,
        max_findings: int = 3,
    ) -> Tuple[Dict[str, Any], TokenUsage]:
        """
        Summarize a paper with the same delimited JSON prompt, budget and
        validation as the hosted clients (review A10/M33). The old text format
        was parsed by splitting on blank lines: single-newline sections put every
        line into key_findings, and "- -5%" lost its minus sign.
        """
        from .llm_providers import SUMMARY_SCHEMA, _build_summary_prompt, _parsed_or_billed
        prompt = _build_summary_prompt(abstract, full_text, self.max_chars, max_findings)
        raw, usage = self.generate(prompt, json_schema=SUMMARY_SCHEMA)
        return _parsed_or_billed(raw, usage), usage


class MockOllamaClient(OllamaClient):
    """Mock Ollama client for testing without running Ollama."""

    def is_available(self) -> bool:
        """Always returns True for testing."""
        return True

    def generate(self, prompt: str, context: Optional[str] = None
                 ) -> Tuple[Optional[str], TokenUsage]:
        """Return mock response. No model ran, so nothing is counted."""
        return "This is a mock response for testing.", UNCOUNTED

    def summarize_paper(
        self,
        abstract: str,
        full_text: str,
        max_findings: int = 3,
    ) -> Tuple[Optional[Dict[str, Any]], TokenUsage]:
        """Return mock summary. No model ran, so nothing is counted."""
        return {
            "key_findings": [
                "Mock finding 1 from the abstract",
                "Mock finding 2 from the text",
                "Mock finding 3 based on context",
            ],
            "methodology": "Mock methodology description based on the paper text.",
            "conclusions": "Mock conclusions and implications inferred from the paper.",
        }, UNCOUNTED
