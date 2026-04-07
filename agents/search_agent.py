"""
Search agent: Load key_terms.json, execute bioRxiv searches, store results.
Callable from GUI or CLI (python agents/search_agent.py --cluster "Name").
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.biorxiv_api import BioRxivAPI
from src.db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class SearchAgent:
    """Agent for searching bioRxiv and storing results."""

    def __init__(self, key_terms_path: str = "key_terms.json", db_path: str = "~/preprints/biorxiv.db"):
        """
        Initialize search agent.

        Args:
            key_terms_path: Path to key_terms.json
            db_path: Path to SQLite database
        """
        self.key_terms_path = Path(key_terms_path)
        self.api = BioRxivAPI()
        self.db = Database(db_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load key_terms.json configuration."""
        try:
            with open(self.key_terms_path, "r") as f:
                config = json.load(f)
            logger.info(f"Loaded config: {self.key_terms_path}")
            return config
        except FileNotFoundError:
            logger.error(f"key_terms.json not found: {self.key_terms_path}")
            return {"search_clusters": {}}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in key_terms.json: {e}")
            return {"search_clusters": {}}

    def search_cluster(
        self, cluster_name: str, dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Execute all enabled searches in a cluster.

        Args:
            cluster_name: Name of search cluster
            dry_run: If True, don't save to database

        Returns:
            Dictionary with results and stats
        """
        clusters = self.config.get("search_clusters", {})

        if cluster_name not in clusters:
            logger.warning(f"Cluster not found: {cluster_name}")
            return {"success": False, "papers_found": 0, "papers_saved": 0}

        cluster = clusters[cluster_name]

        if not cluster.get("enabled", False):
            logger.info(f"Cluster disabled: {cluster_name}")
            return {"success": False, "papers_found": 0, "papers_saved": 0}

        profiles = cluster.get("profiles", [])
        total_papers = 0
        total_saved = 0

        for profile in profiles:
            if not profile.get("enabled", False):
                logger.info(f"Profile disabled: {profile.get('name')}")
                continue

            papers_found, papers_saved = self._execute_search(profile, dry_run)
            total_papers += papers_found
            total_saved += papers_saved

        logger.info(
            f"Cluster '{cluster_name}' complete: {total_papers} papers found, "
            f"{total_saved} saved"
        )

        return {
            "success": True,
            "cluster": cluster_name,
            "papers_found": total_papers,
            "papers_saved": total_saved,
        }

    def search_all_enabled(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute all enabled searches across all clusters.

        Args:
            dry_run: If True, don't save to database

        Returns:
            Dictionary with aggregated results and stats
        """
        clusters = self.config.get("search_clusters", {})
        total_papers = 0
        total_saved = 0
        results = []

        for cluster_name, cluster in clusters.items():
            if not cluster.get("enabled", False):
                logger.info(f"Cluster disabled: {cluster_name}")
                continue

            result = self.search_cluster(cluster_name, dry_run)
            results.append(result)
            total_papers += result.get("papers_found", 0)
            total_saved += result.get("papers_saved", 0)

        logger.info(f"All searches complete: {total_papers} papers found, {total_saved} saved")

        return {
            "success": True,
            "results": results,
            "total_papers_found": total_papers,
            "total_papers_saved": total_saved,
        }

    def _execute_search(self, profile: Dict[str, Any], dry_run: bool = False) -> tuple:
        """
        Execute a single search profile.

        Args:
            profile: Search profile dictionary
            dry_run: If True, don't save to database

        Returns:
            Tuple of (papers_found, papers_saved)
        """
        profile_name = profile.get("name", "Unknown")
        category = profile.get("category")
        days_back = profile.get("days_back", 7)
        keywords = profile.get("keywords", [])
        authors = profile.get("authors", [])

        try:
            logger.info(f"Searching: {profile_name} (last {days_back} days)")

            # Query API
            api_response = self.api.search_recent(
                days=days_back,
                category=category,
                server="biorxiv",
            )

            papers = self.api.parse_papers(api_response)
            logger.info(f"Found {len(papers)} papers for {profile_name}")

            # Filter by keywords if specified
            if keywords:
                papers = self._filter_by_keywords(papers, keywords)
                logger.info(f"Filtered to {len(papers)} papers (keywords: {keywords})")

            # Filter by authors if specified
            if authors:
                papers = self._filter_by_authors(papers, authors)
                logger.info(f"Filtered to {len(papers)} papers (authors: {authors})")

            # Save to database if not dry run
            papers_saved = 0
            if not dry_run:
                for paper in papers:
                    paper_id = self.db.insert_paper(paper)
                    if paper_id:
                        papers_saved += 1
                        logger.debug(f"Saved: {paper.get('title', 'Unknown')}")

            return len(papers), papers_saved

        except Exception as e:
            logger.error(f"Error searching {profile_name}: {e}")
            return 0, 0

    def _filter_by_keywords(
        self, papers: List[Dict[str, Any]], keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Filter papers by keywords (case-insensitive search in title + abstract).

        Args:
            papers: List of paper dictionaries
            keywords: List of keywords to search for

        Returns:
            Filtered list of papers
        """
        filtered = []

        for paper in papers:
            title = (paper.get("title") or "").lower()
            abstract = (paper.get("abstract") or "").lower()
            searchable = f"{title} {abstract}"

            # Match if ANY keyword is found
            if any(keyword.lower() in searchable for keyword in keywords):
                filtered.append(paper)

        return filtered

    def _filter_by_authors(
        self, papers: List[Dict[str, Any]], authors: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Filter papers by author name (case-insensitive substring match).
        Matches against authors and author_corresponding fields.
        """
        filtered = []
        for paper in papers:
            authors_str = (paper.get("authors") or "").lower()
            corresponding = (paper.get("author_corresponding") or "").lower()
            searchable = f"{authors_str} {corresponding}"
            if any(author.lower() in searchable for author in authors):
                filtered.append(paper)
        return filtered


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Search bioRxiv and store results")
    parser.add_argument(
        "--cluster",
        help="Run specific cluster by name",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all enabled clusters",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save to database",
    )
    args = parser.parse_args()

    agent = SearchAgent()

    if args.cluster:
        result = agent.search_cluster(args.cluster, dry_run=args.dry_run)
    elif args.all:
        result = agent.search_all_enabled(dry_run=args.dry_run)
    else:
        # Default: run all enabled
        result = agent.search_all_enabled(dry_run=args.dry_run)

    logger.info(f"Result: {result}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
