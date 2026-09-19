#!/usr/bin/env python3
"""
Headless CLI for searching multiple sources and emitting results as JSON.
Can be driven by cron jobs or external orchestration.

Usage:
  python agents/monitor.py --filter "Agent Simulation" [--dry-run] [--download-dir PATH] [--json PATH] [--max N]
  python agents/monitor.py --all            # every enabled filter
"""

import sys
from pathlib import Path
import json
import argparse
import logging
from typing import Optional

# Allow import of src modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.orchestrator import SourceOrchestrator, FAILURE_STATUS_MARKER, _SOURCE_LABELS
from src.sources.config import load_sources_config
from src.filtering import filter_papers
from src.filters_store import EMPTY_FILTER_MESSAGE, filter_has_text

logger = logging.getLogger(__name__)

# FAILURE_STATUS_MARKER is imported from orchestrator (P19: single source of truth;
# a wording change there is a visible diff that forces this file to update too).


def load_filters(path: str = "filters.json") -> list:
    """Load filters.json and return the list of filter dicts.

    Returns a list (not a dict) so that duplicate filter names are preserved;
    both will run under --all.  Warns when names are duplicated.
    """
    filter_path = Path(path)
    if not filter_path.exists():
        logger.error("filters.json not found at %s", filter_path)
        return []
    try:
        with open(filter_path) as f:
            data = json.load(f)

        # Handle structure: {filters: [{name, enabled, ...}, ...]}
        if isinstance(data, dict) and "filters" in data:
            filters_list = [f for f in data["filters"] if isinstance(f, dict)]
        else:
            # Fallback: data is already a list
            filters_list = data if isinstance(data, list) else []

        # Warn on duplicate names so the user knows --all will run both.
        seen: dict = {}
        for i, f in enumerate(filters_list):
            name = f.get("name", "")
            if name in seen:
                logger.warning(
                    "Duplicate filter name %r at index %d and %d — both will run with --all",
                    name, seen[name], i,
                )
            else:
                seen[name] = i

        return filters_list
    except Exception as e:
        logger.error("Failed to load filters.json: %s", e)
        return []


def find_filter(filters: list, name: str) -> Optional[dict]:
    """Find the first filter matching name."""
    return next((f for f in filters if f.get("name") == name), None)


def get_enabled_filters(filters: list) -> list:
    """Get all enabled filter dicts (may include entries with duplicate names)."""
    return [f for f in filters if f.get("enabled", False)]


def run_search(
    orchestrator: SourceOrchestrator,
    filter_dict: dict,
    filter_name: str,
    max_results: int = 200,
    dry_run: bool = False,
    sources_failed: Optional[list] = None,
) -> list:
    """
    Run a single search and return the deduplicated records that match the filter.

    A source query is only an approximation of a saved filter — each API has its
    own field syntax, and facets like paper_type/version/published/license have
    no equivalent on a preprint server. The GUI therefore re-applies the whole
    filter to what comes back, and so must this CLI, or the same saved filter
    yields a different set depending on which front end ran it.

    Args:
        orchestrator:   SourceOrchestrator instance
        filter_dict:    Filter dict from filters.json
        filter_name:    Name of the filter (for logging)
        max_results:    Maximum results to return
        dry_run:        If True, don't download PDFs
        sources_failed: If provided, failed source names are appended here.

    Returns:
        List of paper dicts (CanonicalRecord.to_dict()) that match the filter.
        An empty filter is skipped with a message and returns [] without
        searching (docs/implementation_plan_2026-09-18_filter_run.md#FR1).
    """
    if not filter_has_text(filter_dict):
        print(f"[{filter_name}] Skipped: {EMPTY_FILTER_MESSAGE}", file=sys.stderr)
        return []

    source_selection = filter_dict.get("source_selection", {"all": True})

    print(f"[{filter_name}] Searching...", file=sys.stderr)

    failed_this_run: list = []

    _label_to_name = {v: k for k, v in _SOURCE_LABELS.items()}

    def on_status(message: str) -> None:
        print(f"[{filter_name}] {message}", file=sys.stderr)
        if FAILURE_STATUS_MARKER in message:
            label = message.split(FAILURE_STATUS_MARKER)[0].strip()
            source_name = _label_to_name.get(label, label)
            failed_this_run.append(source_name)

    records = orchestrator.search(
        filter_dict,
        source_selection=source_selection,
        on_status=on_status,
        max_results=max_results,
    )

    papers = [r.to_dict() for r in records]
    matched = filter_papers(papers, filter_dict)

    print(
        f"[{filter_name}] {len(records)} unique records, "
        f"{len(matched)} match the filter "
        f"({len(papers) - len(matched)} dropped client-side)",
        file=sys.stderr,
    )

    if failed_this_run:
        print(
            f"[{filter_name}] Failed sources: {', '.join(failed_this_run)}",
            file=sys.stderr,
        )
        if sources_failed is not None:
            sources_failed.extend(failed_this_run)

    return matched


def download_pdf(record: dict, dest_dir: Path, timeout: int = 30) -> str:
    """
    Download a record's PDF to dest_dir.

    Returns "ok" if successful or already exists, "skip" if no pdf_url present,
    "fail" on a genuine download error.
    Failures are logged at WARNING (not DEBUG) so they appear in cron logs.
    """
    pdf_url = record.get("pdf_url", "")
    if not pdf_url:
        return "skip"

    # Generate filename: use canonical_id or source_record_id as base
    canonical_id = record.get("canonical_id", "unknown")
    # Slugify: remove colons, keep only alphanumeric and dashes
    slug = canonical_id.replace(":", "_").replace("/", "_")[:50]
    filename = f"{slug}.pdf"
    filepath = dest_dir / filename

    # Skip if already exists
    if filepath.exists():
        return "ok"

    try:
        import requests
        resp = requests.get(pdf_url, timeout=timeout, headers={"User-Agent": "biorx/1.0"})
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        print(f"  Downloaded {filename}", file=sys.stderr)
        return "ok"
    except Exception as e:
        logger.warning("Failed to download %s: %s", pdf_url, e)
        return "fail"


def main(args=None):
    parser = argparse.ArgumentParser(
        description="Headless search across multiple publication sources."
    )
    parser.add_argument(
        "--filter",
        type=str,
        help="Name of a specific filter to run",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all enabled filters",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results but don't write files",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        help="Directory to download PDFs to (default: metadata only)",
    )
    parser.add_argument(
        "--json",
        type=str,
        help="File path to write JSON output to (in addition to stdout JSONL)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=200,
        help="Maximum results per filter (default: 200)",
    )
    parser.add_argument(
        "--filters-path",
        type=str,
        default="filters.json",
        help="Path to filters.json (default: filters.json)",
    )

    parsed = parser.parse_args(args)

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # Load configs
    filters = load_filters(parsed.filters_path)
    if not filters:
        print("No filters loaded; exiting", file=sys.stderr)
        return 1

    config = load_sources_config()
    orchestrator = SourceOrchestrator(config)

    # Determine which filters to run
    filters_to_run = []
    if parsed.filter:
        f = find_filter(filters, parsed.filter)
        if f:
            filters_to_run = [(parsed.filter, f)]
        else:
            print(f"Filter '{parsed.filter}' not found", file=sys.stderr)
            return 1
    elif parsed.all:
        enabled = get_enabled_filters(filters)
        filters_to_run = [(f["name"], f) for f in enabled]
    else:
        parser.print_help()
        return 1

    # Setup download directory if requested
    download_dir = None
    if parsed.download_dir and not parsed.dry_run:
        download_dir = Path(parsed.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        print(f"PDFs will be downloaded to: {download_dir}", file=sys.stderr)

    # Run searches and emit results
    all_records = []
    all_sources_failed: list = []
    total_downloaded = 0
    total_failed_downloads = 0

    for filter_name, filter_dict in filters_to_run:
        records = run_search(
            orchestrator,
            filter_dict,
            filter_name,
            max_results=parsed.max,
            dry_run=parsed.dry_run,
            sources_failed=all_sources_failed,
        )

        # Emit as JSONL to stdout (records are already filtered plain dicts)
        for record_dict in records:
            print(json.dumps(record_dict, separators=(",", ":")))

            # Download PDF if requested
            if download_dir and not parsed.dry_run:
                result = download_pdf(record_dict, download_dir)
                if result == "ok":
                    total_downloaded += 1
                elif result == "fail":
                    total_failed_downloads += 1
                # "skip" (no pdf_url) → neither counter

        all_records.extend(records)

    # Write full JSON output if requested
    if parsed.json and not parsed.dry_run:
        output_path = Path(parsed.json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(all_records, f, indent=2)
        print(f"Wrote {len(all_records)} records to {output_path}", file=sys.stderr)

    print(f"Total: {len(all_records)} matching records across all filters", file=sys.stderr)

    # PDF download summary (M2: failures must not be silent)
    if download_dir:
        print(
            f"PDFs: {total_downloaded} downloaded / {total_failed_downloads} failed",
            file=sys.stderr,
        )

    # Exit 2 on any source failure or PDF download failure (P2: nonzero exit for cron)
    if all_sources_failed:
        print(
            f"Sources failed: {', '.join(all_sources_failed)}",
            file=sys.stderr,
        )
        return 2

    if total_failed_downloads > 0:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
