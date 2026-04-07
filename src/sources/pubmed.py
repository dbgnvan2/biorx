"""
PubMed search adapter.
Wraps Europe PMC adapter with a focus on MEDLINE/PubMed content.
"""

from __future__ import annotations
from typing import Sequence, Optional, Dict, Any

from .base import RawRecord
from .schema import CanonicalRecord
from .europepmc import EuropePmcAdapter

class PubMedAdapter(EuropePmcAdapter):
    """
    Search adapter for PubMed content via the Europe PMC API.
    Forces SRC:MED in queries to ensure PubMed results.
    """

    source_name = "pubmed"
    source_trust_weight = 1.0

    def search(
        self, query: str, page: int = 1, page_size: int = 25
    ) -> Sequence[RawRecord]:
        """Search PubMed via Europe PMC."""
        # Ensure SRC:MED is in the query if not already
        pm_query = query
        if "SRC:MED" not in query.upper():
            if " AND " in query.upper() or " OR " in query.upper():
                pm_query = f"({query}) AND SRC:MED"
            else:
                pm_query = f"{query} AND SRC:MED"
        
        return super().search(pm_query, page=page, page_size=page_size)

    def normalize(self, raw: RawRecord) -> CanonicalRecord:
        """Normalize and adjust labeling to PubMed."""
        record = super().normalize(raw)
        # If it has a PMID, it's a PubMed record
        if record.pmid:
            record.journal_or_server = f"{record.journal_or_server} (PubMed)"
        return record
