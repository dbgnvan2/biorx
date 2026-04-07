"""
bioRxiv API wrapper for searching preprints.
Handles API requests, pagination, and response parsing.
"""

import requests
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.biorxiv.org"


class BioRxivAPI:
    """Wrapper for bioRxiv REST API."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    def search_by_date_range(
        self,
        start_date: str,
        end_date: str,
        category: Optional[str] = None,
        server: str = "biorxiv",
        cursor: int = 0,
        format: str = "json",
    ) -> Dict[str, Any]:
        """
        Search for papers within a date range.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            category: Optional bioRxiv category (e.g., 'genetics', 'virology')
            server: 'biorxiv' or 'medrxiv'
            cursor: Pagination cursor (0 = first page)
            format: Response format ('json' or 'xml')

        Returns:
            Dictionary with papers list and metadata
        """
        # Format dates for API interval parameter: start_date/end_date
        interval = f"{start_date}/{end_date}"

        url = f"{BASE_URL}/details/{server}/{interval}/{cursor}/{format}"

        params = {}
        if category:
            params["category"] = category

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise

    def search_recent(
        self,
        days: int = 7,
        category: Optional[str] = None,
        server: str = "biorxiv",
        cursor: int = 0,
        format: str = "json",
    ) -> Dict[str, Any]:
        """
        Search for recent papers (last N days).

        Args:
            days: Number of days to look back
            category: Optional bioRxiv category
            server: 'biorxiv' or 'medrxiv'
            cursor: Pagination cursor
            format: Response format

        Returns:
            Dictionary with papers list and metadata
        """
        end   = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = f"{BASE_URL}/details/{server}/{start}/{end}/{cursor}/{format}"

        params = {}
        if category:
            params["category"] = category

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise

    def get_published(
        self,
        start_date: str,
        end_date: str,
        server: str = "biorxiv",
        cursor: int = 0,
        format: str = "json",
    ) -> Dict[str, Any]:
        """
        Search for published papers (that were preprints).

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            server: 'biorxiv' or 'medrxiv'
            cursor: Pagination cursor
            format: Response format

        Returns:
            Dictionary with published papers
        """
        interval = f"{start_date}/{end_date}"
        url = f"{BASE_URL}/pubs/{server}/{interval}/{cursor}"

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise

    def search_by_funder(
        self,
        funder_ror_id: str,
        start_date: str,
        end_date: str,
        server: str = "biorxiv",
        cursor: int = 0,
        format: str = "json",
    ) -> Dict[str, Any]:
        """
        Search for papers by funder (ROR ID).

        Args:
            funder_ror_id: Funder ROR identifier
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            server: 'biorxiv' or 'medrxiv'
            cursor: Pagination cursor
            format: Response format

        Returns:
            Dictionary with papers
        """
        interval = f"{start_date}.{end_date}"
        url = f"{BASE_URL}/funder/{server}/{interval}/{funder_ror_id}/{cursor}/{format}"

        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise

    def parse_papers(self, api_response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse API response into structured paper objects.

        Args:
            api_response: Raw response from bioRxiv API

        Returns:
            List of paper dictionaries with normalized fields
        """
        papers = []

        # API returns papers in 'collection' key
        if "collection" not in api_response:
            return papers

        for paper in api_response["collection"]:
            parsed = {
                "doi": paper.get("doi"),
                "title": paper.get("title"),
                "authors": paper.get("authors", []),
                "abstract": paper.get("abstract"),
                "pub_date": paper.get("date"),
                "category": paper.get("category"),
                "url": paper.get("url"),
                "version": paper.get("version"),
                "license": paper.get("license"),
                "funding": paper.get("funding", []),
                "server": paper.get("server"),
            }
            papers.append(parsed)

        return papers

    def close(self):
        """Close HTTP session."""
        self.session.close()
