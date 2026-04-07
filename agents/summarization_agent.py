"""
Summarization agent: Find unsummarized papers, extract text, generate summaries with Qwen.
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
from src.llm import OllamaClient, MockOllamaClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class SummarizationAgent:
    """Agent for summarizing papers using Qwen."""

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

        if use_mock:
            self.llm = MockOllamaClient()
            logger.info("Using mock LLM client")
        else:
            self.llm = OllamaClient()

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

        # Check if Ollama is available
        if not self.llm.is_available():
            logger.error(
                "Ollama not available. Ensure it's running: ollama serve"
            )
            return {
                "success": False,
                "error": "Ollama not available",
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

            # Check if PDF exists
            if not pdf_path or not Path(pdf_path).exists():
                logger.warning(f"PDF not found for paper {paper_id}: {pdf_path}")
                return False

            # Extract text from PDF
            logger.debug(f"Extracting text from {pdf_path}")
            text = self.pdf_handler.extract_text(pdf_path, max_pages=10)

            if not text:
                logger.warning(f"Failed to extract text from {pdf_path}")
                return False

            # Prepare input for LLM
            abstract = paper.get("abstract", "")
            full_text = text[:5000]  # Limit to first 5000 chars

            # Generate summary
            logger.debug(f"Generating summary with Qwen for paper {paper_id}")
            summary_data = self.llm.summarize_paper(abstract, full_text)

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
            )

            logger.info(f"Summary saved for paper {paper_id}")
            return True

        except Exception as e:
            logger.error(f"Error summarizing paper {paper_id}: {e}")
            return False


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Summarize papers using Qwen")
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

    if args.paper_id:
        success = agent.summarize_paper_by_id(args.paper_id)
        result = {"success": success, "paper_id": args.paper_id}
    else:
        result = agent.summarize_all_unsummarized(max_count=args.max_count)

    logger.info(f"Result: {result}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
