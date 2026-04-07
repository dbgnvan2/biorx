"""
Ollama/Qwen interface for summarization.
"""

import requests
import json
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen:7b"


class OllamaClient:
    """Client for Ollama API."""

    def __init__(self, base_url: str = OLLAMA_URL, model: str = OLLAMA_MODEL):
        """
        Initialize Ollama client.

        Args:
            base_url: Ollama API base URL
            model: Model name (e.g., 'qwen:7b')
        """
        self.base_url = base_url
        self.model = model

    def is_available(self) -> bool:
        """
        Check if Ollama is running and model is available.

        Returns:
            True if Ollama is reachable
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            logger.warning("Ollama is not available. Ensure it's running on localhost:11434")
            return False

    def generate(self, prompt: str, context: Optional[str] = None) -> Optional[str]:
        """
        Generate text using Ollama.

        Args:
            prompt: Instruction prompt
            context: Optional context/document text

        Returns:
            Generated text, or None if failed
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

            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()

            result = response.json()
            return result.get("response", "").strip()

        except requests.RequestException as e:
            logger.error(f"Ollama generation failed: {e}")
            return None

    def summarize_paper(
        self,
        abstract: str,
        full_text: str,
        max_findings: int = 3,
    ) -> Optional[Dict[str, Any]]:
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
{full_text[:3000]}  # Limit to first 3000 chars to avoid token limits

Provide ONLY the following structured output (no markdown, plain text):

KEY FINDINGS:
- Finding 1
- Finding 2
- Finding 3

METHODOLOGY:
Brief description of the research methods used.

CONCLUSIONS:
Brief description of the conclusions and implications."""

        response = self.generate(prompt)
        if not response:
            return None

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

            return result

        except Exception as e:
            logger.error(f"Failed to parse summarization response: {e}")
            return None


class MockOllamaClient(OllamaClient):
    """Mock Ollama client for testing without running Ollama."""

    def is_available(self) -> bool:
        """Always returns True for testing."""
        return True

    def generate(self, prompt: str, context: Optional[str] = None) -> Optional[str]:
        """Return mock response."""
        return "This is a mock response for testing."

    def summarize_paper(
        self,
        abstract: str,
        full_text: str,
        max_findings: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """Return mock summary."""
        return {
            "key_findings": [
                "Mock finding 1 from the abstract",
                "Mock finding 2 from the text",
                "Mock finding 3 based on context",
            ],
            "methodology": "Mock methodology description based on the paper text.",
            "conclusions": "Mock conclusions and implications inferred from the paper.",
        }
