"""
SourceOrchestrator: coordinates all source adapters, deduplication, and enrichment.

Orchestration flow (per spec):
  1. Read source picker state
  2. Resolve active search sources
  3. Query each active source (sequential, paginated)
  4. Normalize each result to CanonicalRecord
  5. Deduplicate across sources
  6. Enrich with Crossref where metadata is missing
  7. Resolve OA via Unpaywall (if DOI exists)
  8. Apply source-based ranking
  9. Return canonical records (or stream via on_batch)
"""

from __future__ import annotations
import logging
from typing import Dict, Any, List, Callable, Optional

from .schema import CanonicalRecord
from .dedup import Deduplicator
from .query_builder import build_europepmc_query, build_psyarxiv_query, get_date_range
from .errors import SourceUnavailableError, RateLimitedError
from .config import (
    get_enabled_search_sources, is_source_enabled,
    get_unpaywall_email, get_crossref_user_agent,
)

logger = logging.getLogger(__name__)

# Source trust weights for ranking (peer-reviewed > PMC-backed > preprints)
_SOURCE_TRUST: Dict[str, float] = {
    "europepmc":       1.00,
    "pubmed":          1.00,
    "crossref":        0.85,
    "psyarxiv":        0.75,
    "socarxiv":        0.75,
    "biorxiv_medrxiv": 0.75,
    "openalex":        0.70,
}


class SourceOrchestrator:
    """
    Coordinates searches across multiple publication sources.
    Instantiate once at app startup; pass to SearchWorker for each search.
    """

    MAX_PAGES_PER_SOURCE = 20     # max pages fetched per source per search
    PAGE_SIZE            = 50     # papers per page per source

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._search_adapters: Dict[str, Any] = {}
        self._crossref  = None
        self._unpaywall = None
        self._register_adapters()

    def _register_adapters(self) -> None:
        """Instantiate and register enabled adapters."""
        if is_source_enabled(self.config, "europepmc"):
            from .europepmc import EuropePmcAdapter
            self._search_adapters["europepmc"] = EuropePmcAdapter()
            logger.info("Registered adapter: europepmc")

        if is_source_enabled(self.config, "pubmed"):
            from .pubmed import PubMedAdapter
            self._search_adapters["pubmed"] = PubMedAdapter()
            logger.info("Registered adapter: pubmed")

        if is_source_enabled(self.config, "psyarxiv"):
            from .psyarxiv import PsyArxivAdapter
            self._search_adapters["psyarxiv"] = PsyArxivAdapter()
            logger.info("Registered adapter: psyarxiv")

        if is_source_enabled(self.config, "socarxiv"):
            from .socarxiv import SocArxivAdapter
            self._search_adapters["socarxiv"] = SocArxivAdapter()
            logger.info("Registered adapter: socarxiv")

        if is_source_enabled(self.config, "biorxiv_medrxiv"):
            from .biorxiv_medrxiv import BiorxivMedrxivAdapter
            self._search_adapters["biorxiv_medrxiv"] = BiorxivMedrxivAdapter()
            logger.info("Registered adapter: biorxiv_medrxiv")

        if is_source_enabled(self.config, "crossref"):
            from .crossref import CrossrefAdapter
            self._crossref = CrossrefAdapter(
                user_agent=get_crossref_user_agent(self.config)
            )

        if is_source_enabled(self.config, "unpaywall"):
            from .unpaywall import UnpaywallAdapter
            self._unpaywall = UnpaywallAdapter(
                email=get_unpaywall_email(self.config)
            )

    # ── Public search API ─────────────────────────────────────────────────────

    def search(
        self,
        filter_dict: Dict[str, Any],
        source_selection: Optional[Dict[str, Any]] = None,
        on_batch: Optional[Callable[[List[CanonicalRecord]], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        max_results: int = 2000,
    ) -> List[CanonicalRecord]:
        """
        Execute a multi-source search and return deduplicated CanonicalRecords.

        Args:
            filter_dict:      GUI filter dict (text_groups, days_back, etc.)
            source_selection: {"all": bool, "selected": list[str]}
            on_batch:         Callback called with each batch of new records.
            on_progress:      Callback called with (fetched_so_far, total_estimate).
            should_stop:      Callable returning True when search should abort.
            max_results:      Maximum total records to return.

        Returns:
            List of deduplicated, ranked CanonicalRecords.
        """
        if source_selection is None:
            source_selection = {"all": True, "selected": []}

        active = self._resolve_active_sources(source_selection)
        if not active:
            logger.warning("No active sources — restoring All Sources")
            active = list(self._search_adapters.keys())

        dedup        = Deduplicator()
        total_fetched = 0

        for source_name in active:
            if should_stop and should_stop():
                break

            adapter = self._search_adapters[source_name]
            query   = self._build_query(source_name, filter_dict)
            logger.info("Searching %s: %s", source_name, query[:80])

            try:
                fetched = self._search_source(
                    source_name=source_name,
                    adapter=adapter,
                    query=query,
                    filter_dict=filter_dict,
                    dedup=dedup,
                    on_batch=on_batch,
                    on_progress=on_progress,
                    should_stop=should_stop,
                    max_results=max_results - total_fetched,
                )
                total_fetched += fetched
            except SourceUnavailableError as e:
                logger.error("Source unavailable (%s): %s", source_name, e)
                continue
            except Exception as e:
                logger.error("Unexpected error from %s: %s", source_name, e, exc_info=True)
                continue

            if total_fetched >= max_results:
                break

        # Enrichment phase (only for records that have DOIs)
        records = dedup.results()
        self._enrich(records)

        return self._rank(records)

    # ── Source routing ────────────────────────────────────────────────────────

    def _resolve_active_sources(self, selection: Dict[str, Any]) -> List[str]:
        """Return ordered list of source names to query."""
        if selection.get("all", True):
            return list(self._search_adapters.keys())
        selected = selection.get("selected", [])
        active   = [s for s in selected if s in self._search_adapters]
        if not active:
            # Safety: never allow zero sources
            return list(self._search_adapters.keys())
        return active

    def _build_query(self, source_name: str, filter_dict: Dict[str, Any]) -> str:
        """Convert filter_dict into a source-specific query string."""
        if source_name in ("europepmc", "pubmed"):
            return build_europepmc_query(filter_dict)
        if source_name in ("psyarxiv", "socarxiv"):
            return build_psyarxiv_query(filter_dict)
        # bioRxiv/medRxiv is date-based, query is informational only
        return build_psyarxiv_query(filter_dict)

    # ── Per-source paginated search ────────────────────────────────────────────

    def _search_source(
        self,
        source_name: str,
        adapter: Any,
        query: str,
        filter_dict: Dict[str, Any],
        dedup: Deduplicator,
        on_batch: Optional[Callable],
        on_progress: Optional[Callable],
        should_stop: Optional[Callable],
        max_results: int,
    ) -> int:
        """Paginate through a single source and add results to dedup. Returns count fetched."""
        fetched = 0
        page    = 1

        while page <= self.MAX_PAGES_PER_SOURCE:
            if should_stop and should_stop():
                break
            if fetched >= max_results:
                break

            try:
                # PsyArXiv, SocArXiv and bioRxiv adapters accept filter_dict for date range
                if source_name in ("psyarxiv", "socarxiv", "biorxiv_medrxiv"):
                    raw_records = adapter.search(
                        query, page=page, page_size=self.PAGE_SIZE,
                        filter_dict=filter_dict
                    )
                else:
                    raw_records = adapter.search(query, page=page, page_size=self.PAGE_SIZE)
            except RateLimitedError:
                logger.warning("Rate limited by %s — stopping", source_name)
                break
            except SourceUnavailableError as e:
                logger.error("Source %s unavailable: %s", source_name, e)
                break

            if not raw_records:
                break

            batch: List[CanonicalRecord] = []
            for raw in raw_records:
                try:
                    canonical = adapter.normalize(raw)
                    canonical = dedup.add(canonical)
                    batch.append(canonical)
                    fetched += 1
                except Exception as e:
                    logger.debug("Normalization error (%s): %s", source_name, e)
                    continue

            if batch and on_batch:
                on_batch(batch)

            if on_progress:
                on_progress(fetched, fetched)  # total unknown until all pages done

            # bioRxiv returns raw dicts with _total attached
            if source_name == "biorxiv_medrxiv" and raw_records:
                total = raw_records[0].get("_total", 0) if isinstance(raw_records[0], dict) else 0
                if on_progress and total:
                    on_progress(fetched, total)
                if total and fetched >= total:
                    break

            if len(raw_records) < self.PAGE_SIZE:
                break  # last page

            page += 1

        logger.info("Source %s: %d records fetched", source_name, fetched)
        return fetched

    # ── Enrichment ─────────────────────────────────────────────────────────────

    def _enrich(self, records: List[CanonicalRecord]) -> None:
        """Run Crossref and Unpaywall enrichment on records that have DOIs."""
        for record in records:
            if not record.doi:
                continue
            try:
                if self._crossref:
                    self._crossref.enrich(record)
            except Exception as e:
                logger.debug("Crossref error for %s: %s", record.doi, e)
            try:
                if self._unpaywall:
                    self._unpaywall.enrich(record)
            except Exception as e:
                logger.debug("Unpaywall error for %s: %s", record.doi, e)

    # ── Ranking ────────────────────────────────────────────────────────────────

    def _rank(self, records: List[CanonicalRecord]) -> List[CanonicalRecord]:
        """
        Apply source-based ranking. Returns records sorted by trust weight descending.

        Rules (per spec):
        1. Prefer peer-reviewed over preprints by default
        2. Prefer records with DOI
        3. Prefer records with abstract
        4. Penalize retracted / corrected
        5. Prefer records with legal OA access
        """
        def score(r: CanonicalRecord) -> float:
            s = r.source_trust_weight
            if r.doi:         s += 0.05
            if r.abstract:    s += 0.03
            if r.best_oa_url: s += 0.02
            if r.flags.retracted or r.flags.corrected:
                s -= 0.50
            return s

        return sorted(records, key=score, reverse=True)

    def get_enabled_sources(self) -> List[str]:
        """Return names of all registered search sources."""
        return list(self._search_adapters.keys())
