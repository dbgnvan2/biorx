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
from .query_builder import build_europepmc_query, build_psyarxiv_query, build_arxiv_query
from .errors import SourceUnavailableError, RateLimitedError
from .config import (
    SOURCE_LABELS, get_enabled_search_sources, is_source_enabled, source_label,
    get_unpaywall_email, get_crossref_user_agent,
)

logger = logging.getLogger(__name__)

# Marker present in every failure status message emitted via on_status.
# monitor.py imports this so a wording change is a visible diff, not silent drift (P19).
FAILURE_STATUS_MARKER = "— skipped"

class EmptyFilterError(ValueError):
    """The filter has no text term or author, so every source would return its
    whole date window unfiltered. Refused here, in the engine every entry point
    uses, not only in each front end (review S2, learnings P10)."""


def _report_failure(source_name: str, kind: str,
                    on_status: Optional[Callable[[str], None]],
                    on_source_failure: Optional[Callable[[str, str], None]]) -> None:
    """Tell callers a source failed: a status line for people, and the
    (source, kind) pair for code, so no caller parses the line (review S2)."""
    if on_status:
        on_status(f"{source_label(source_name)} {FAILURE_STATUS_MARKER} ({kind})")
    if on_source_failure:
        on_source_failure(source_name, kind)


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

        # A source enabled in sources_config.yaml with no adapter here (OpenAlex
        # is only an abstract lookup) would otherwise do nothing, silently (M31).
        for name in get_enabled_search_sources(self.config):
            if name not in self._search_adapters:
                msg = (f"{source_label(name)} is enabled in sources_config.yaml "
                       "but has no search adapter, so it is not searched.")
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
        enrich_only: Optional[Callable[[CanonicalRecord], bool]] = None,
        on_enrich_progress: Optional[Callable[[int, int], None]] = None,
        on_enrich_problem: Optional[Callable[[str, int, int], None]] = None,
        on_source_failure: Optional[Callable[[str, str], None]] = None,
    ) -> List[CanonicalRecord]:
        """
        Execute a multi-source search and return deduplicated CanonicalRecords.

        Args:
            filter_dict:      filter dict (text_groups, days_back, etc.)
            source_selection: {"all": bool, "selected": list[str]}
            on_batch:         Callback called with each batch of new records.
            on_progress:      Callback called with (fetched_so_far, total_estimate).
            on_status:        Callback called with a human-readable phase string
                              (e.g. "Searching Europe PMC…", "Enriching 120 papers…").
            should_stop:      Callable returning True when search should abort.
            max_results:      Maximum records read from each source. A source
                              cut off by it (or by MAX_PAGES_PER_SOURCE) is
                              reported through on_status as truncated.
            enrich_only:      If given, only records it accepts are enriched.
                              Callers that filter locally pass their match test
                              so a run does not spend two HTTP calls on every
                              paper it is about to discard. None = enrich all.
            on_enrich_progress: Callback with (enriched_so_far, to_enrich). If
                              absent, enrichment reports through on_progress.
            on_enrich_problem: Callback with (service label, failed, attempted)
                              for each enrichment service whose lookups failed,
                              so an outage is shown rather than only logged.
            on_source_failure: Callback with (source name, kind) for each
                              source that failed or was cut short; kind is
                              unavailable, error, rate-limited, partial or
                              truncated. Callers use this, not the status text.

        Raises:
            EmptyFilterError: the filter has nothing to search for.

        Returns:
            List of deduplicated, ranked CanonicalRecords.
        """
        # Every entry point runs the same preconditions here (review S2): the
        # canonical filter shape for the query builders, and no empty filter.
        from src.filtering import normalise_filter
        from src.filters_store import EMPTY_FILTER_MESSAGE, filter_has_text
        filter_dict = normalise_filter(filter_dict)
        if not filter_has_text(filter_dict):
            raise EmptyFilterError(EMPTY_FILTER_MESSAGE)
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

            adapter = self._adapter_for_search(self._search_adapters[source_name])
            query   = self._build_query(source_name, filter_dict)
            label   = source_label(source_name)
            logger.info("Searching %s: %s", source_name, query[:80])
            if on_status:
                on_status(f"Searching {label}…")

            # max_results applies to each source on its own. One shared budget
            # let the first source (Europe PMC) use it all, so later sources
            # were never queried and nothing said so (review B1).
            budget = max_results

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
                    on_status=on_status,
                    on_source_failure=on_source_failure,
                )
                total_fetched += fetched
                known_total   += fetched
                if on_status:
                    on_status(f"{label}: {fetched:,} fetched")
            except SourceUnavailableError as e:
                logger.error("Source unavailable (%s): %s", source_name, e)
                _report_failure(source_name, "unavailable", on_status, on_source_failure)
                continue
            except Exception as e:
                logger.error("Unexpected error from %s: %s", source_name, e, exc_info=True)
                _report_failure(source_name, "error", on_status, on_source_failure)
                continue

        # Enrichment phase (only for records that have DOIs)
        records = dedup.results()
        self._enrich(records, on_status=on_status,
                     on_progress=on_enrich_progress or on_progress,
                     should_stop=should_stop, enrich_only=enrich_only,
                     on_problem=on_enrich_problem)

        return self._rank(records)

    @staticmethod
    def _adapter_for_search(adapter: Any) -> Any:
        """Return the adapter instance one search should use.

        Purpose: Keep per-search paging state out of the shared adapters.
        Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B6
        Tests:   tests/test_orchestrator.py::test_b6_concurrent_searches_do_not_share_cursor

        Adapters keep cursors, totals and page sizes on the instance, and one
        orchestrator serves every job thread. Real adapters define for_search()
        to hand out a fresh instance. The check is on the class so a test
        double (MagicMock answers every attribute) is used as it is.
        """
        if callable(getattr(type(adapter), "for_search", None)):
            return adapter.for_search()
        return adapter

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

    def _page_limit(self, source_name: str) -> int:
        """Pages read from one source per search: sources_config.yaml
        publication_sources.<name>.max_pages, else MAX_PAGES_PER_SOURCE.

        Spec:  docs/cycles/2026-09-29_browser-run.md (bioRxiv decision)
        Tests: tests/test_orchestrator.py::test_br3_page_limit_from_config
        """
        section = ((self.config or {}).get("publication_sources") or {}).get(source_name) or {}
        try:
            return max(1, int(section.get("max_pages", self.MAX_PAGES_PER_SOURCE)))
        except (TypeError, ValueError):
            logger.warning("publication_sources.%s.max_pages is not a number; using %d",
                           source_name, self.MAX_PAGES_PER_SOURCE)
            return self.MAX_PAGES_PER_SOURCE

    @staticmethod
    def _local_matcher(filter_dict: Dict[str, Any]) -> Callable[[CanonicalRecord], bool]:
        """The filter's match test for one record, as front ends apply it after
        a search — without the licence condition, which enrichment may still
        supply (review B5); the caller applies the full filter afterwards.

        Spec:  docs/cycles/2026-09-29_browser-run.md (bioRxiv decision)
        Tests: tests/test_orchestrator.py::test_br3_biorxiv_budget_counts_matches
        """
        from src.filtering import filter_papers, without_license
        f = without_license(filter_dict)
        return lambda rec: bool(filter_papers([rec.to_dict()], f, normalised=True))

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
        on_status: Optional[Callable] = None,
        on_source_failure: Optional[Callable[[str, str], None]] = None,
    ) -> int:
        """Paginate through a single source and add results to dedup. Returns count fetched.

        Purpose: Read one source up to its limits, and say so when a limit cut it off.
        Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B1
        Tests:   tests/test_orchestrator.py::test_b1_truncated_source_is_reported,
                 tests/test_orchestrator.py::test_b1_page_cap_truncation_is_reported
        """
        fetched   = 0
        page      = 1
        unreadable = 0       # records normalize() could not read
        # A source whose API cannot search the words (bioRxiv/medRxiv reads a
        # date range) is filtered here, page by page: only matches count
        # against max_results, and its own page limit bounds the reading. It
        # used to spend the budget on the first papers of the window, nearly
        # none of which matched (browser run 2026-09-29).
        local_filter = getattr(adapter, "filters_locally", False) is True
        matches = self._local_matcher(filter_dict) if local_filter else None
        page_limit = self._page_limit(source_name)
        not_matching = 0
        duplicates = 0       # matched, but a paper already read (another version)
        limited_by_pages = False
        seen_raw  = 0        # records the source sent, duplicates included
        src_total = 0
        last_page_full = False
        limited   = False    # stopped by max_results or the page cap, not by the source

        while True:
            if page > page_limit:
                limited = True
                limited_by_pages = True
                break
            if should_stop and should_stop():
                break
            if fetched >= max_results:
                limited = True
                break

            # Say which page is being asked for before the request goes out: a
            # slow source (PsyArXiv, with one request per title term) used to
            # leave "Searching…" unchanged for minutes, which looked hung.
            if on_status and not local_filter:
                of_total = f" of {src_total:,}" if src_total else ""
                on_status(f"{source_label(source_name)}: reading page {page}"
                          f"{' (' + format(fetched, ',') + of_total + ' so far)' if fetched else ''}…")
            if on_status and hasattr(adapter, "on_activity"):
                adapter.on_activity = lambda msg, _l=source_label(source_name): on_status(f"{_l}: {msg}")

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
                _report_failure(source_name, "rate-limited", on_status, on_source_failure)
                break
            except SourceUnavailableError as e:
                logger.error("Source %s unavailable: %s", source_name, e)
                if fetched == 0:
                    raise  # propagate so search() can emit "— skipped (unavailable)"
                # Mid-pagination failure: records already yielded but source is now broken.
                # Emit the marker so callers (monitor.py) count this as a source failure.
                _report_failure(source_name, "partial", on_status, on_source_failure)
                break

            # An adapter may filter entries out of a page (arXiv drops withdrawn
            # papers), so the number of records returned is not the number the
            # source sent. Page-end detection must use the source's own page
            # size or a full page minus one withdrawn paper ends the search.
            page_size_seen = getattr(adapter, "last_page_size", None)
            if not isinstance(page_size_seen, int):
                page_size_seen = len(raw_records)

            if not page_size_seen:
                break
            seen_raw += page_size_seen
            # An adapter that knows whether its source has more says so
            # (has_more); a page shorter than ours is then not the end. The
            # bioRxiv API sends 30 at a time, so "shorter than 50" stopped it
            # after one page (browser run 2026-09-29).
            reported_more = getattr(adapter, "has_more", None)
            if isinstance(reported_more, bool):
                last_page_full = reported_more
            else:
                last_page_full = page_size_seen >= self.PAGE_SIZE

            if not raw_records:
                # Whole page filtered out but the source has more — keep paging.
                if not last_page_full:
                    break
                page += 1
                continue

            batch: List[CanonicalRecord] = []
            for raw in raw_records:
                try:
                    canonical = adapter.normalize(raw)
                    if matches is not None and not matches(canonical):
                        not_matching += 1
                        continue
                    before    = len(dedup)
                    canonical = dedup.add(canonical)
                    if len(dedup) == before:
                        # Duplicate of an already-seen record (e.g. the heavy
                        # EuropePMC/PubMed overlap). It was merged into the
                        # existing record — don't re-stream or re-count it, so
                        # the page's result count matches the unique set that is
                        # actually saved. Counted, so "N read" adds up.
                        duplicates += 1
                        continue
                    batch.append(canonical)
                    fetched += 1
                except Exception as e:
                    # Counted and reported below: a schema change at a source
                    # must not look like a source with no results (M20).
                    unreadable += 1
                    logger.debug("Normalization error (%s): %s", source_name, e)
                    continue

            if batch and on_batch:
                on_batch(batch)

            # Determine this source's total hit count when the source reports one,
            # so the progress bar can advance against a real target (spec E2.2).
            # Every adapter that knows its hit count reports it as last_total.
            src_total = getattr(adapter, "last_total", None)
            if not isinstance(src_total, int):
                src_total = 0   # the adapter does not report a total

            if local_filter:
                # One channel, one quantity (QA gate 2026-09-29, P12): the
                # progress count is matches, as for every other source, with
                # no known total; how far through the window it is goes on
                # the status line.
                if on_progress:
                    on_progress(fetched, 0)
                if on_status:
                    of_total = f" of {src_total:,}" if src_total else ""
                    on_status(f"{source_label(source_name)}: {seen_raw:,}{of_total} "
                              f"papers read, {fetched:,} match so far…")
            elif on_progress:
                on_progress(fetched, src_total)  # src_total == 0 means "unknown"

            if src_total and (seen_raw if local_filter else fetched) >= src_total:
                break

            if not last_page_full:
                break  # last page

            page += 1

        logger.info("Source %s: %d records fetched", source_name, fetched)
        if local_filter:
            of_total = f" of {src_total:,}" if src_total else ""
            # Every paper read is one of these; say so, and warn if they do
            # not add up (production run 2026-09-29: 149 + 4,924 of 5,095
            # left 22 unexplained).
            logger.info("Source %s: read %d%s papers — %d match the filter, %d do not, "
                        "%d repeat a paper already read, %d could not be read",
                        source_name, seen_raw, of_total, fetched, not_matching,
                        duplicates, unreadable)
            accounted = fetched + not_matching + duplicates + unreadable
            if accounted != seen_raw:
                logger.warning("Source %s: %d papers read but %d accounted for",
                               source_name, seen_raw, accounted)
            if on_status:
                repeats = (f" ({duplicates:,} more were repeats of a paper already read)"
                           if duplicates else "")
                on_status(f"{source_label(source_name)}: {seen_raw:,}{of_total} papers read, "
                          f"{fetched:,} match the filter{repeats}")
        if unreadable:
            label = source_label(source_name)
            got = seen_raw or unreadable
            logger.warning("Source %s: %d of %d records could not be read",
                           source_name, unreadable, got)
            if on_status:
                on_status(f"{label}: {unreadable:,} of {got:,} records could not be read")
            if unreadable >= got:     # every record failed, not just some
                _report_failure(source_name, "error", on_status, on_source_failure)
        more_left = (seen_raw < src_total) if src_total else last_page_full
        if limited and more_left:
            label = source_label(source_name)
            of_total = f" of {src_total:,}" if src_total else ""
            logger.warning("Source %s truncated: %d%s read", source_name, seen_raw, of_total)
            by_pages = limited_by_pages and local_filter
            if on_status:
                why = "page limit reached" if by_pages else "result limit reached"
                on_status(f"{label}: {seen_raw:,}{of_total} read ({why})")
            _report_failure(source_name, "page-limit" if by_pages else "truncated",
                            on_status, on_source_failure)
        return fetched

    # ── Enrichment ─────────────────────────────────────────────────────────────

    def _enrich(
        self,
        records: List[CanonicalRecord],
        on_status: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        enrich_only: Optional[Callable[[CanonicalRecord], bool]] = None,
        on_problem: Optional[Callable[[str, int, int], None]] = None,
    ) -> None:
        """Run Crossref and Unpaywall enrichment on records that have DOIs.

        Emits status/progress so the page is not silent during this phase, which
        makes up to two synchronous HTTP calls per DOI (spec E2.3).

        Spec:  docs/implementation_plan_2026-09-18_filter_run.md#C2
        Tests: tests/test_orchestrator.py::test_fr2_1_enrich_only_limits_enrichment,
               tests/test_orchestrator.py::test_i3_enrichment_outage_is_reported

        A lookup that failed (adapter returned False, or raised) is counted per
        service and reported once at the end — as a WARNING, an on_status line
        and on_problem — so missing PDF links are not silent (P2).
        """
        if not (self._crossref or self._unpaywall):
            return  # nothing to enrich against

        targets = [r for r in records
                   if r.doi and (enrich_only is None or enrich_only(r))]
        if not targets:
            return

        total = len(targets)
        logger.info("Enriching %d records via Crossref/Unpaywall", total)
        if on_status:
            on_status(f"Enriching {total:,} papers…")
        if on_progress:
            on_progress(0, total)

        failed = {"Crossref": 0, "Unpaywall": 0}   # lookup failed: retry later
        errors = {"Crossref": 0, "Unpaywall": 0}   # our code raised: a bug (M19)
        attempted = 0
        for i, record in enumerate(targets, start=1):
            if should_stop and should_stop():
                break
            attempted = i
            for label, adapter in (("Crossref", self._crossref), ("Unpaywall", self._unpaywall)):
                if not adapter:
                    continue
                try:
                    if adapter.enrich(record) is False:
                        failed[label] += 1
                except Exception:
                    # The adapters turn network and service failures into a
                    # False return. An exception here is a defect in reading
                    # the response, not an outage, so it is not counted as one.
                    errors[label] += 1
                    logger.warning("%s enrichment raised for %s", label, record.doi,
                                   exc_info=True)
            # Update every 25 records (and on the final one) to limit UI churn.
            if on_progress and (i % 25 == 0 or i == total):
                on_progress(i, total)
            if on_status and (i % 25 == 0 or i == total):
                on_status(f"Enriching {i:,}/{total:,} papers…")

        for label, count in failed.items():
            if not count:
                continue
            logger.warning("%s lookups failed for %d of %d papers — their PDF links "
                           "or metadata may be missing", label, count, attempted)
            if on_status:
                on_status(f"{label} failed for {count:,} of {attempted:,} papers")
            if on_problem:
                on_problem(label, count, attempted)

        for label, count in errors.items():
            if count and on_status:
                on_status(f"{label} lookups hit a program error for {count:,} of "
                          f"{attempted:,} papers (details in the server log)")

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
