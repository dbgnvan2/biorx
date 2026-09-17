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
from .query_builder import build_europepmc_query, build_psyarxiv_query, build_arxiv_query, get_date_range
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
    "arxiv":           0.75,
    "openalex":        0.70,
}

# Human-readable source names for status messages (spec E2.1)
_SOURCE_LABELS: Dict[str, str] = {
    "europepmc":       "Europe PMC",
    "pubmed":          "PubMed",
    "crossref":        "Crossref",
    "psyarxiv":        "PsyArXiv",
    "socarxiv":        "SocArXiv",
    "biorxiv_medrxiv": "bioRxiv/medRxiv",
    "arxiv":           "arXiv",
    "openalex":        "OpenAlex",
}


def _source_label(source_name: str) -> str:
    """Return a display label for a source, falling back to the raw name."""
    return _SOURCE_LABELS.get(source_name, source_name)


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
        self.warnings: list = []   # surfaceable startup conditions; readable by callers
        self._register_adapters()

    def _register_adapters(self) -> None:
        """Instantiate and register enabled adapters."""
        if is_source_enabled(self.config, "europepmc"):
            from .europepmc import EuropePmcAdapter
            self._search_adapters["europepmc"] = EuropePmcAdapter(sources_config=self.config)
            logger.info("Registered adapter: europepmc")

        if is_source_enabled(self.config, "pubmed"):
            from .pubmed import PubMedAdapter
            self._search_adapters["pubmed"] = PubMedAdapter(sources_config=self.config)
            logger.info("Registered adapter: pubmed")

        if is_source_enabled(self.config, "psyarxiv"):
            from .psyarxiv import PsyArxivAdapter
            self._search_adapters["psyarxiv"] = PsyArxivAdapter(sources_config=self.config)
            logger.info("Registered adapter: psyarxiv")

        if is_source_enabled(self.config, "socarxiv"):
            from .socarxiv import SocArxivAdapter
            self._search_adapters["socarxiv"] = SocArxivAdapter(sources_config=self.config)
            logger.info("Registered adapter: socarxiv")

        if is_source_enabled(self.config, "biorxiv_medrxiv"):
            from .biorxiv_medrxiv import BiorxivMedrxivAdapter
            self._search_adapters["biorxiv_medrxiv"] = BiorxivMedrxivAdapter(sources_config=self.config)
            logger.info("Registered adapter: biorxiv_medrxiv")

        if is_source_enabled(self.config, "arxiv"):
            from .arxiv import ArxivAdapter
            self._search_adapters["arxiv"] = ArxivAdapter(sources_config=self.config)
            logger.info("Registered adapter: arxiv")

        if is_source_enabled(self.config, "crossref"):
            from .crossref import CrossrefAdapter
            self._crossref = CrossrefAdapter(
                user_agent=get_crossref_user_agent(self.config)
            )

        if is_source_enabled(self.config, "unpaywall"):
            email = get_unpaywall_email(self.config)
            if email:
                from .unpaywall import UnpaywallAdapter
                self._unpaywall = UnpaywallAdapter(email=email)
            else:
                # Unpaywall requires a real address. Say so rather than send a
                # placeholder or skip quietly (learnings P2).
                msg = (
                    "Open-access lookup (Unpaywall) is off: no contact email. "
                    "Set BIORX_CONTACT_EMAIL or contact_email in sources_config.yaml."
                )
                logger.warning(msg)
                self.warnings.append(msg)

        # All polite-pool APIs degrade to bare biorx/1.0 UA when no contact email.
        # OpenAlex always runs (via paper_meta.py); other sources are config-gated.
        # Surface this unconditionally so callers and the web app can warn users (P2/P5).
        from .config import get_contact_email
        if not get_contact_email(self.config):
            msg = (
                "Requests to polite-pool APIs (Crossref, arXiv, Europe PMC, PubMed, "
                "PsyArXiv, SocArXiv, bioRxiv/medRxiv, OpenAlex) will send no contact "
                "email (BIORX_CONTACT_EMAIL not set). Set BIORX_CONTACT_EMAIL or "
                "contact_email in sources_config.yaml."
            )
            logger.warning(msg)
            self.warnings.append(msg)

    # ── Public search API ─────────────────────────────────────────────────────

    def search(
        self,
        filter_dict: Dict[str, Any],
        source_selection: Optional[Dict[str, Any]] = None,
        on_batch: Optional[Callable[[List[CanonicalRecord]], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
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
            on_status:        Callback called with a human-readable phase string
                              (e.g. "Searching Europe PMC…", "Enriching 120 papers…").
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

        dedup         = Deduplicator()
        total_fetched = 0     # records fetched across all completed sources
        known_total   = 0     # sum of completed sources' realized totals (for the bar)

        for source_name in active:
            if should_stop and should_stop():
                break

            adapter = self._search_adapters[source_name]
            query   = self._build_query(source_name, filter_dict)
            label   = _source_label(source_name)
            logger.info("Searching %s: %s", source_name, query[:80])
            if on_status:
                on_status(f"Searching {label}…")

            budget = max_results - total_fetched

            # Per-source progress wrapper: translate this source's local
            # (fetched, total) into cumulative numbers so the bar advances
            # smoothly across the whole multi-source run (spec E2.2).
            def on_source_progress(src_fetched, src_total,
                                   _baseline=total_fetched, _known=known_total,
                                   _budget=budget):
                if not on_progress:
                    return
                g_fetched = _baseline + src_fetched
                eff_total = min(src_total, _budget) if src_total else src_fetched
                g_total   = _known + max(eff_total, src_fetched)
                on_progress(g_fetched, max(g_fetched, g_total))

            try:
                fetched = self._search_source(
                    source_name=source_name,
                    adapter=adapter,
                    query=query,
                    filter_dict=filter_dict,
                    dedup=dedup,
                    on_batch=on_batch,
                    on_progress=on_source_progress,
                    should_stop=should_stop,
                    max_results=budget,
                )
                total_fetched += fetched
                known_total   += fetched
                if on_status:
                    on_status(f"{label}: {fetched:,} fetched")
            except SourceUnavailableError as e:
                logger.error("Source unavailable (%s): %s", source_name, e)
                if on_status:
                    on_status(f"{label} unavailable — skipped")
                continue
            except Exception as e:
                logger.error("Unexpected error from %s: %s", source_name, e, exc_info=True)
                if on_status:
                    on_status(f"{label} error — skipped")
                continue

            if total_fetched >= max_results:
                break

        # Enrichment phase (only for records that have DOIs)
        records = dedup.results()
        self._enrich(records, on_status=on_status, on_progress=on_progress,
                     should_stop=should_stop)

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
        if source_name == "arxiv":
            return build_arxiv_query(filter_dict)
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
                # PsyArXiv, SocArXiv, bioRxiv and arXiv adapters accept filter_dict for date range
                if source_name in ("psyarxiv", "socarxiv", "biorxiv_medrxiv", "arxiv"):
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
                if fetched == 0:
                    raise  # propagate so search() can emit "unavailable — skipped"
                break

            # An adapter may filter entries out of a page (arXiv drops withdrawn
            # papers), so the number of records returned is not the number the
            # source sent. Page-end detection must use the source's own page
            # size or a full page minus one withdrawn paper ends the search.
            page_size_seen = getattr(adapter, "last_page_size", None)
            if page_size_seen is None:
                page_size_seen = len(raw_records)

            if not page_size_seen:
                break

            if not raw_records:
                # Whole page filtered out but the source has more — keep paging.
                if page_size_seen < self.PAGE_SIZE:
                    break
                page += 1
                continue

            batch: List[CanonicalRecord] = []
            for raw in raw_records:
                try:
                    canonical = adapter.normalize(raw)
                    before    = len(dedup)
                    canonical = dedup.add(canonical)
                    if len(dedup) == before:
                        # Duplicate of an already-seen record (e.g. the heavy
                        # EuropePMC/PubMed overlap). It was merged into the
                        # existing record — don't re-stream or re-count it, so
                        # the GUI's result count matches the unique set that is
                        # actually saved.
                        continue
                    batch.append(canonical)
                    fetched += 1
                except Exception as e:
                    logger.debug("Normalization error (%s): %s", source_name, e)
                    continue

            if batch and on_batch:
                on_batch(batch)

            # Determine this source's total hit count when the source reports one,
            # so the progress bar can advance against a real target (spec E2.2).
            src_total = 0
            if source_name == "biorxiv_medrxiv" and raw_records:
                # bioRxiv returns raw dicts with _total attached
                if isinstance(raw_records[0], dict):
                    src_total = raw_records[0].get("_total", 0) or 0
            else:
                # EuropePMC/PubMed expose hitCount as adapter.last_total
                src_total = getattr(adapter, "last_total", None) or 0

            if on_progress:
                on_progress(fetched, src_total)  # src_total == 0 means "unknown"

            if src_total and fetched >= src_total:
                break

            if page_size_seen < self.PAGE_SIZE:
                break  # last page

            page += 1

        logger.info("Source %s: %d records fetched", source_name, fetched)
        return fetched

    # ── Enrichment ─────────────────────────────────────────────────────────────

    def _enrich(
        self,
        records: List[CanonicalRecord],
        on_status: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Run Crossref and Unpaywall enrichment on records that have DOIs.

        Emits status/progress so the GUI is not silent during this phase, which
        makes up to two synchronous HTTP calls per DOI (spec E2.3).
        """
        if not (self._crossref or self._unpaywall):
            return  # nothing to enrich against

        targets = [r for r in records if r.doi]
        if not targets:
            return

        total = len(targets)
        logger.info("Enriching %d records via Crossref/Unpaywall", total)
        if on_status:
            on_status(f"Enriching {total:,} papers…")
        if on_progress:
            on_progress(0, total)

        for i, record in enumerate(targets, start=1):
            if should_stop and should_stop():
                break
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
            # Update every 25 records (and on the final one) to limit UI churn.
            if on_progress and (i % 25 == 0 or i == total):
                on_progress(i, total)
            if on_status and (i % 25 == 0 or i == total):
                on_status(f"Enriching {i:,}/{total:,} papers…")

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
