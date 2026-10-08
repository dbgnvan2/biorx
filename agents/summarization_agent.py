"""
Summarization agent: Find unsummarized papers, extract text, generate summaries
with the provider llm_config.yaml names as default (default_provider).
Run from the command line (python agents/summarization_agent.py).
"""

import sys
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_values import config_int
from src.db import Database
from src.pdf_handler import PDFHandler
from src.llm import MockOllamaClient
from src.tokens import UNCOUNTED
from src.llm_config import load_llm_config
from src.llm_providers import LLMError, resolve_client
from src.summarize import summarize_paper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class SummarizationAgent:
    """Agent for summarizing papers with the configured default provider."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        use_mock: bool = False,
    ):
        """
        Initialize summarization agent.

        Args:
            db_path: Path to SQLite database. None = the same database the web
                app uses (BIORX_DB_PATH / DATA_DIR, else ~/preprints/biorxiv.db).
                A hard-coded default skipped those settings (review A12).
            use_mock: If True, use mock LLM (for testing without Ollama). Needs
                an explicit db_path: mock findings written to the real
                database would be shown to every user as a summary (M25).
        """
        if use_mock and not db_path:
            raise ValueError("--mock writes made-up findings; give --db-path to a "
                             "scratch database, never the real one")
        self.db = Database(db_path)
        self.pdf_handler = PDFHandler()
        self.use_mock = use_mock
        # Resolved on first use, not here: a missing API key must not stop the
        # agent from starting (it may have nothing to summarize).
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
            max_downloads=config_int(settings, "max_downloads", 4,
                                 name="full_text.max_downloads"),
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

    def _find_text(self, paper: Dict[str, Any]):
        """The downloaded PDF if there is one, else a free copy found online."""
        pdf_path = paper.get("pdf_path")
        if pdf_path and Path(pdf_path).exists():
            text = self.pdf_handler.extract_text(pdf_path, max_pages=10) or ""
            if text:
                return text, "used", "downloaded PDF"
        found = self._find_full_text(paper)
        if found.text:
            return found.text, "used", found.source
        return "", found.explain(), ""

    def _summarize_paper(self, paper: Dict[str, Any]) -> bool:
        """
        Summarize a single stored paper through the shared pipeline
        (src/summarize.py, review S1).

        Returns True only when a summary (or the abstract stand-in) was stored.
        """
        paper_id = paper.get("id")
        logger.info("Summarizing: %s", paper.get("title", "Unknown"))
        if paper_id is not None:
            self.db.mark_summary_attempt(paper_id)
        try:
            outcome = summarize_paper(
                self.db, paper, lambda: (self._client(), self.model),
                find_text=self._find_text,
                llm_config=load_llm_config(),
                on_usage=lambda u: setattr(self, "last_usage", u))
        except Exception as e:
            logger.error("Error summarizing paper %s: %s", paper_id, e)
            return False
        if not outcome.saved:
            logger.error("Paper %s: summary not saved — %s", paper_id, outcome.not_saved_reason)
            return False
        if outcome.reused:
            logger.info("Paper %s already has a full-text summary; kept it", paper_id)
        elif outcome.source_text == "abstract":
            logger.info("Paper %s: no full text found — kept the abstract, no model used",
                        paper_id)
        else:
            logger.info("Summary saved for paper %s", paper_id)
        return True


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
        "--db-path",
        type=str,
        default=None,
        help="Database to use (default: BIORX_DB_PATH / DATA_DIR, as the web app). "
             "Required with --mock.",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        help="Summarize specific paper by ID",
    )
    args = parser.parse_args()

    try:
        agent = SummarizationAgent(db_path=args.db_path, use_mock=args.mock)
    except ValueError as e:
        print(f"Cannot summarize: {e}", file=sys.stderr)
        return 2

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
