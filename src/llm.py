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

    def generate(self, prompt: str, context: Optional[str] = None
                 ) -> Tuple[Optional[str], TokenUsage]:
        """
        Generate text using Ollama.

        Args:
            prompt: Instruction prompt
            context: Optional context/document text

        Returns:
            (generated text, what the call cost). The text is None if the call
            failed. Ollama does not always report counts, so the usage may be
            uncounted even on success — uncounted means unknown, not free
            (M1.A.1).
        """
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n{prompt}"

        try:
            url = f"{self.base_url}/api/generate"
            payload = {
                "model": self.model,
                "prompt": full_prompt,
                "stream": False,
            }

            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()

            result = response.json()
            return result.get("response", "").strip(), from_ollama(result)

        except requests.RequestException as e:
            logger.error(f"Ollama generation failed: {e}")
            return None, UNCOUNTED

    def summarize_paper(
        self,
        abstract: str,
        full_text: str,
        max_findings: int = 3,
    ) -> Tuple[Optional[Dict[str, Any]], TokenUsage]:
        """
        Summarize a paper using Qwen.

        Args:
            abstract: Paper abstract
            full_text: Full paper text
            max_findings: Maximum number of key findings to extract

        Returns:
            Dictionary with key_findings, methodology, conclusions
        """
        prompt = f"""You are a research paper summarization expert. Analyze the following paper and provide a structured summary.

PAPER ABSTRACT:
{abstract}

PAPER TEXT:
{full_text[:self.max_chars]}

Provide ONLY the following structured output (no markdown, plain text):

KEY FINDINGS:
- Finding 1
- Finding 2
- Finding 3

METHODOLOGY:
Brief description of the research methods used.

CONCLUSIONS:
Brief description of the conclusions and implications."""

        response, usage = self.generate(prompt)
        if not response:
            return None, usage

        # Parse response
        try:
            result = {
                "key_findings": [],
                "methodology": "",
                "conclusions": "",
            }

            sections = response.split("\n\n")

            for section in sections:
                if section.startswith("KEY FINDINGS:"):
                    findings_text = section.replace("KEY FINDINGS:", "").strip()
                    for line in findings_text.split("\n"):
                        finding = line.lstrip("- ").strip()
                        if finding:
                            result["key_findings"].append(finding)

                elif section.startswith("METHODOLOGY:"):
                    result["methodology"] = (
                        section.replace("METHODOLOGY:", "").strip()
                    )

                elif section.startswith("CONCLUSIONS:"):
                    result["conclusions"] = section.replace("CONCLUSIONS:", "").strip()

            return result, usage

        except Exception as e:
            # The model ran and was billed; the tokens are reported even though
            # the text could not be parsed (M1.B.3).
            logger.error(f"Failed to parse summarization response: {e}")
            return None, usage


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
