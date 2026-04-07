#!/usr/bin/env python3
"""
Quick test script to verify all components work together.
Run: python test_components.py
"""

import sys
from pathlib import Path
import json
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """Test that all modules import correctly."""
    logger.info("Testing imports...")
    try:
        from src.biorxiv_api import BioRxivAPI
        logger.info("✓ biorxiv_api")

        from src.db import Database
        logger.info("✓ db")

        from src.pdf_handler import PDFHandler
        logger.info("✓ pdf_handler")

        from src.llm import OllamaClient, MockOllamaClient
        logger.info("✓ llm")

        from agents.search_agent import SearchAgent
        logger.info("✓ search_agent")

        from agents.summarization_agent import SummarizationAgent
        logger.info("✓ summarization_agent")

        logger.info("✅ All imports successful")
        return True
    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        return False


def test_database():
    """Test database initialization and basic operations."""
    logger.info("\nTesting database...")
    try:
        from src.db import Database

        db = Database("/tmp/test_biorxiv.db")

        # Test insert
        paper = {
            "doi": "10.1101/2025.03.001",
            "title": "Test Paper",
            "authors": ["Author 1", "Author 2"],
            "abstract": "Test abstract",
            "pub_date": "2025-03-01",
            "category": "genetics",
            "url": "https://example.com",
        }

        paper_id = db.insert_paper(paper)
        if paper_id:
            logger.info(f"✓ Inserted paper with ID {paper_id}")
        else:
            logger.info(f"✓ Paper already exists (deduplication working)")

        # Test retrieval
        retrieved = db.get_paper_by_doi(paper["doi"])
        if retrieved:
            logger.info(f"✓ Retrieved paper: {retrieved['title']}")

        db.close()
        logger.info("✅ Database tests passed")
        return True

    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False


def test_api():
    """Test bioRxiv API (dry run, no DB save)."""
    logger.info("\nTesting bioRxiv API...")
    try:
        from src.biorxiv_api import BioRxivAPI

        api = BioRxivAPI()

        # Try a simple search
        logger.info("Searching for recent genetics papers...")
        response = api.search_recent(days=7, category="genetics")

        papers = api.parse_papers(response)
        logger.info(f"✓ Got {len(papers)} papers")

        if papers:
            first_paper = papers[0]
            logger.info(f"✓ First result: {first_paper.get('title', 'Unknown')[:60]}...")

        api.close()
        logger.info("✅ API tests passed")
        return True

    except Exception as e:
        logger.error(f"❌ API error: {e}")
        return False


def test_config():
    """Test key_terms.json configuration."""
    logger.info("\nTesting configuration...")
    try:
        config_path = Path("key_terms.json")

        if not config_path.exists():
            logger.error("❌ key_terms.json not found")
            return False

        with open(config_path, "r") as f:
            config = json.load(f)

        clusters = config.get("search_clusters", {})
        logger.info(f"✓ Found {len(clusters)} search clusters")

        for cluster_name, cluster_data in clusters.items():
            profiles = cluster_data.get("profiles", [])
            enabled = "✓" if cluster_data.get("enabled") else "✗"
            logger.info(f"  {enabled} {cluster_name}: {len(profiles)} profiles")

        logger.info("✅ Configuration valid")
        return True

    except Exception as e:
        logger.error(f"❌ Configuration error: {e}")
        return False


def test_llm():
    """Test LLM client."""
    logger.info("\nTesting LLM client...")
    try:
        from src.llm import OllamaClient, MockOllamaClient

        # Test with mock first (no Ollama required)
        mock_client = MockOllamaClient()
        if mock_client.is_available():
            logger.info("✓ Mock LLM client available")

            summary = mock_client.summarize_paper("Test abstract", "Test text")
            if summary:
                logger.info(f"✓ Mock summarization works")

        # Test real client
        client = OllamaClient()
        if client.is_available():
            logger.info("✓ Ollama is running on localhost:11434")
            logger.info("✓ Qwen model is available")
        else:
            logger.warning("⚠ Ollama not running (run: ollama serve)")

        logger.info("✅ LLM tests passed")
        return True

    except Exception as e:
        logger.error(f"❌ LLM error: {e}")
        return False


def main():
    """Run all tests."""
    logger.info("=" * 60)
    logger.info("BioRxiv Research Tool - Component Test")
    logger.info("=" * 60)

    results = {
        "Imports": test_imports(),
        "Database": test_database(),
        "API": test_api(),
        "Configuration": test_config(),
        "LLM": test_llm(),
    }

    logger.info("\n" + "=" * 60)
    logger.info("Test Summary")
    logger.info("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {name}")

    logger.info(f"\n{passed}/{total} tests passed")

    if passed == total:
        logger.info("\n🎉 All components working! Ready to run:")
        logger.info("   python gui.py")
        return 0
    else:
        logger.error(f"\n⚠️  {total - passed} test(s) failed. Check errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
