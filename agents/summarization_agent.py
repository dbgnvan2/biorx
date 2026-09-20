"""
Summarization agent: Find unsummarized papers, extract text, generate summaries
with the provider llm_config.yaml names as default (default_provider).
Callable from GUI or CLI (python agents/summarization_agent.py).
"""

import sys
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import Database
from src.pdf_handler import PDFHandler
from src.llm import MockOllamaClient
from src.tokens import UNCOUNTED
from src.llm_config import load_llm_config, max_text_chars
from src.llm_providers import LLMError, ProviderResponseError, _coerce_summary, resolve_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class SummarizationAgent:
    """Agent for summarizing papers with the configured default provider."""

    def __init__(
        self,
        db_path: str = "~/preprints/biorxiv.db",
        use_mock: bool = False,
    ):
        """
        Initialize summarization agent.

        Args:
            db_path: Path to SQLite database
            use_mock: If True, use mock LLM (for testing without Ollama)
        """
        self.db = Database(db_path)
        self.pdf_handler = PDFHandler()
        self.use_mock = use_mock
        # Resolved on first use, not here: the GUI builds this agent at start-up
        # and a missing API key must not stop the app from opening.
        self.llm = MockOllamaClient() if use_mock else None
        self.model = "mock" if use_mock else ""
        self.provider = "mock" if use_mock else ""
        self.key_source = "none"     # "owner"/"user" means a paid, keyed API
        # What the most recent summarize call cost (M1.A.1). Declared here so
        # it always exists, rather than appearing only after a successful run.
        self.last_usage = UNCOUNTED
        if use_mock:
            logger.info("Using mock LLM client")

    def _client(self):
        """Purpose: The client for the configured default provider, and its model.
        Spec:    docs/implementation_plan_2026-09-18_filter_run.md#M1
        Tests:   tests/test_summarization_agent.py::test_m1_agent_uses_the_configured_default,
                 tests/test_summarization_agent.py::test_m1_summary_records_the_model_that_ran

        Raises LLMError (e.g. no API key) with a message for the user.
        """
        if self.llm is None:
            resolved = resolve_client(config=load_llm_config())
            self.llm, self.model = resolved.client, resolved.model
            self.provider, self.key_source = resolved.provider, resolved.key_source
            logger.info("Summaries will use %s (%s)", resolved.provider, resolved.model)
        return self.llm

    def _find_full_text(self, paper: Dict[str, Any]):
        """The same finder chain the web app uses: own link, Unpaywall,
        OpenAlex, Semantic Scholar (src/fulltext.py)."""
        from src.fulltext import download_pdf_text, find_full_text
        from src.paper_meta import pdf_url
        from src.sources.config import (get_unpaywall_email, load_sources_config,
                                        polite_user_agent)
        cfg = load_sources_config()
        settings = cfg.get("full_text") or {}
        own = pdf_url(paper)
        return find_full_text(
            paper, download_pdf_text, own_links=[own] if own else [],
            by_title=bool(settings.get("find_by_title", True)),
            email=get_unpaywall_email(cfg),
            max_downloads=int(settings.get("max_downloads", 4)),
            user_agent=polite_user_agent(cfg),
        )

    def summarize_all_unsummarized(self, max_count: int = 10) -> Dict[str, Any]:
        """
        Summarize all papers that don't have summaries yet.

        Args:
            max_count: Maximum number of papers to summarize

        Returns:
            Dictionary with results and stats
        """
        papers = self.db.get_unsummarized_papers(limit=max_count)
        logger.info(f"Found {len(papers)} unsummarized papers")

        if not papers:
            return {
                "success": True,
                "summarized_count": 0,
                "failed_count": 0,
            }

        try:
            llm = self._client()
            available = llm.is_available()
            problem = "" if available else f"{self.model} is not available"
        except LLMError as e:
            problem = str(e)
        if problem:
            logger.error("Summaries cannot run: %s", problem)
            return {
                "success": False,
                "error": problem,
                "summarized_count": 0,
                "failed_count": len(papers),
            }

        summarized_count = 0
        failed_count = 0

        for paper in papers:
            success = self._summarize_paper(paper)
            if success:
                summarized_count += 1
            else:
                failed_count += 1

        logger.info(
            f"Summarization complete: {summarized_count} successful, "
            f"{failed_count} failed"
        )

        return {
            "success": True,
            "summarized_count": summarized_count,
            "failed_count": failed_count,
        }

    def summarize_paper_by_id(self, paper_id: int) -> bool:
        """
        Summarize a specific paper by ID.

        Args:
            paper_id: Database ID of the paper

        Returns:
            True if successful
        """
        paper = self.db.get_paper_by_id(paper_id)

        if not paper:
            logger.warning(f"Paper not found: {paper_id}")
            return False

        return self._summarize_paper(paper)

    def _summarize_paper(self, paper: Dict[str, Any]) -> bool:
        """
        Summarize a single paper.

        Args:
            paper: Paper dictionary from database

        Returns:
            True if successful
        """
        paper_id = paper.get("id")
        title = paper.get("title", "Unknown")
        pdf_path = paper.get("pdf_path")

        try:
            logger.info(f"Summarizing: {title}")

            # Full text: the downloaded PDF if there is one, else a free copy
            # found online (plan 2026-09-19 C2).
            text, text_source = "", ""
            if pdf_path and Path(pdf_path).exists():
                logger.debug(f"Extracting text from {pdf_path}")
                text = self.pdf_handler.extract_text(pdf_path, max_pages=10) or ""
                text_source = "downloaded PDF" if text else ""
            if not text:
                found = self._find_full_text(paper)
                text, text_source = found.text, found.source
                if not text:
                    logger.info("Paper %s: no full text — %s", paper_id, found.explain())

            abstract = paper.get("abstract", "")
            if not text:
                # No full text: keep the abstract as the entry and do not call
                # the model (plan 2026-09-19 C1, FT1.5).
                if not abstract.strip():
                    logger.warning("Paper %s has neither full text nor an abstract", paper_id)
                    return False
                self.db.insert_summary(paper_id=paper_id, summary_text=abstract,
                                       model_version="", source_text="abstract")
                logger.info("Paper %s: no full text found — kept the abstract, no model used",
                            paper_id)
                return True
            # Same budget the web app uses (llm_config.yaml max_text_chars);
            # what is dropped is logged rather than cut silently (P9).
            budget = max_text_chars(load_llm_config())
            if len(text) > budget:
                logger.info("Paper %s: sending %d of %d extracted characters",
                            paper_id, budget, len(text))
            full_text = text[:budget]

            llm = self._client()
            logger.debug(f"Generating summary with {self.model} for paper {paper_id}")
            summary_data, usage = llm.summarize_paper(abstract, full_text)
            self.last_usage = usage
            if summary_data is None:
                raise ProviderResponseError(f"{self.model} returned no summary")
            # Same check the web app applies: Ollama's text parser returns a
            # dict of empty fields when the reply is not in the expected shape,
            # which must not be saved as a success (review finding 4).
            summary_data = _coerce_summary(summary_data)

            if not summary_data:
                logger.warning(f"Failed to generate summary for paper {paper_id}")
                return False

            # Format key findings
            key_findings = summary_data.get("key_findings", [])
            summary_text = f"KEY FINDINGS:\n"
            for finding in key_findings:
                summary_text += f"- {finding}\n"
            summary_text += f"\nMETHODOLOGY:\n{summary_data.get('methodology', '')}\n"
            summary_text += f"\nCONCLUSIONS:\n{summary_data.get('conclusions', '')}"

            # Save to database
            self.db.insert_summary(
                paper_id=paper_id,
                summary_text=summary_text,
                key_findings=key_findings,
                methodology=summary_data.get("methodology"),
                conclusions=summary_data.get("conclusions"),
                model_version=self.model,
                source_text="full_text",
                text_source=text_source,
            )

            logger.info(f"Summary saved for paper {paper_id}")
            return True

        except Exception as e:
            logger.error(f"Error summarizing paper {paper_id}: {e}")
            return False


def main():
    """CLI entry point."""
    import argparse
    from src.env_file import load_project_env
    load_project_env()      # DEEPSEEK_API_KEY etc. from .env (review finding 3)

    parser = argparse.ArgumentParser(
        description="Summarize papers with the default provider in llm_config.yaml")
    parser.add_argument(
        "--max-count",
        type=int,
        default=10,
        help="Maximum number of papers to summarize",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock LLM (for testing)",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        help="Summarize specific paper by ID",
    )
    args = parser.parse_args()

    agent = SummarizationAgent(use_mock=args.mock)

    # Say which model will run, and whether it is billed, before any call:
    # the default is now a paid API (review finding 7).
    if not args.mock:
        try:
            agent._client()
        except LLMError as e:
            print(f"Cannot summarize: {e}", file=sys.stderr)
            return 1
        count = 1 if args.paper_id else args.max_count
        paid = " — a paid API, billed per paper" if agent.key_source in ("owner", "user") else ""
        print(f"Summarizing up to {count} paper(s) with {agent.provider} ({agent.model}){paid}.",
              file=sys.stderr)

    if args.paper_id:
        success = agent.summarize_paper_by_id(args.paper_id)
        result = {"success": success, "paper_id": args.paper_id}
    else:
        result = agent.summarize_all_unsummarized(max_count=args.max_count)

    logger.info(f"Result: {result}")
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
