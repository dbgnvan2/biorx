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

# Allow import of src modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.orchestrator import SourceOrchestrator
from src.sources.config import load_sources_config
from src.filtering import filter_papers

logger = logging.getLogger(__name__)


def load_filters(path: str = "filters.json") -> dict:
    """Load filters.json and return a dict of {name: filter_dict}."""
    filter_path = Path(path)
    if not filter_path.exists():
        logger.error("filters.json not found at %s", filter_path)
        return {}
    try:
        with open(filter_path) as f:
            data = json.load(f)

        # Handle structure: {filters: [{name, enabled, ...}, ...]}
        if isinstance(data, dict) and "filters" in data:
            return {f["name"]: f for f in data["filters"] if isinstance(f, dict)}
        # Fallback: data is already {name: filter}
        return data
    except Exception as e:
        logger.error("Failed to load filters.json: %s", e)
        return {}


def find_filter(filters: dict, name: str) -> dict | None:
    """Find a filter by name."""
    return filters.get(name)


def get_enabled_filters(filters: dict) -> list:
    """Get all enabled filter names."""
    return [name for name, f in filters.items() if f.get("enabled", False)]


def run_search(
    orchestrator: SourceOrchestrator,
    filter_dict: dict,
    filter_name: str,
    max_results: int = 200,
    dry_run: bool = False,
) -> list:
    """
    Run a single search and return the deduplicated records that match the filter.

    A source query is only an approximation of a saved filter — each API has its
    own field syntax, and facets like paper_type/version/published/license have
    no equivalent on a preprint server. The GUI therefore re-applies the whole
    filter to what comes back, and so must this CLI, or the same saved filter
    yields a different set depending on which front end ran it.

    Args:
        orchestrator: SourceOrchestrator instance
        filter_dict: Filter dict from filters.json
        filter_name: Name of the filter (for logging)
        max_results: Maximum results to return
        dry_run: If True, don't download PDFs

    Returns:
        List of paper dicts (CanonicalRecord.to_dict()) that match the filter.
    """
    source_selection = filter_dict.get("source_selection", {"all": True})

    print(f"[{filter_name}] Searching...", file=sys.stderr)

    records = orchestrator.search(
        filter_dict,
        source_selection=source_selection,
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
    return matched


def download_pdf(record: dict, dest_dir: Path, timeout: int = 30) -> bool:
    """
    Download a record's PDF to dest_dir.

    Returns True if successful or file already exists, False on error.
    """
    pdf_url = record.get("pdf_url", "")
    if not pdf_url:
        return False

    # Generate filename: use canonical_id or source_record_id as base
    canonical_id = record.get("canonical_id", "unknown")
    # Slugify: remove colons, keep only alphanumeric and dashes
    slug = canonical_id.replace(":", "_").replace("/", "_")[:50]
    filename = f"{slug}.pdf"
    filepath = dest_dir / filename

    # Skip if already exists
    if filepath.exists():
        return True

    try:
        import requests
        resp = requests.get(pdf_url, timeout=timeout)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        print(f"  Downloaded {filename}", file=sys.stderr)
        return True
    except Exception as e:
        logger.debug("Failed to download %s: %s", pdf_url, e)
        return False


def main():
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

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # Load configs
    filters = load_filters()
    if not filters:
        print("No filters loaded; exiting", file=sys.stderr)
        return 1

    config = load_sources_config()
    orchestrator = SourceOrchestrator(config)

    # Determine which filters to run
    filters_to_run = []
    if args.filter:
        f = find_filter(filters, args.filter)
        if f:
            filters_to_run = [(args.filter, f)]
        else:
            print(f"Filter '{args.filter}' not found", file=sys.stderr)
            return 1
    elif args.all:
        enabled = get_enabled_filters(filters)
        filters_to_run = [(name, filters[name]) for name in enabled]
    else:
        parser.print_help()
        return 1

    # Setup download directory if requested
    download_dir = None
    if args.download_dir and not args.dry_run:
        download_dir = Path(args.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        print(f"PDFs will be downloaded to: {download_dir}", file=sys.stderr)

    # Run searches and emit results
    all_records = []

    for filter_name, filter_dict in filters_to_run:
        records = run_search(
            orchestrator,
            filter_dict,
            filter_name,
            max_results=args.max,
            dry_run=args.dry_run,
        )

        # Emit as JSONL to stdout (records are already filtered plain dicts)
        for record_dict in records:
            print(json.dumps(record_dict, separators=(",", ":")))

            # Download PDF if requested
            if download_dir and not args.dry_run:
                download_pdf(record_dict, download_dir)

        all_records.extend(records)

    # Write full JSON output if requested
    if args.json and not args.dry_run:
        output_path = Path(args.json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(all_records, f, indent=2)
        print(f"Wrote {len(all_records)} records to {output_path}", file=sys.stderr)

    print(f"Total: {len(all_records)} matching records across all filters", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
