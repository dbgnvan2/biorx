"""
Sources configuration loader.
Reads sources_config.yaml and exposes feature-flag helpers.
"""

from pathlib import Path
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG: Dict[str, Any] = {
    "publication_sources": {
        "europepmc":        {"enabled": True,  "default_selected": True},
        "pubmed":           {"enabled": True,  "default_selected": True},
        "psyarxiv":         {"enabled": True,  "default_selected": True},
        "socarxiv":         {"enabled": True,  "default_selected": True},
        "biorxiv_medrxiv":  {"enabled": True,  "default_selected": False},
        "openalex":         {"enabled": False, "default_selected": False},
        "crossref":         {"enabled": True},
        "unpaywall":        {"enabled": True},
        "pmc_oa":           {"enabled": False},
    },
    "unpaywall_email": "research@example.com",
    "crossref_user_agent": "ResearchTool/1.0",
}

# Sources that are search-capable (shown in picker)
_SEARCH_SOURCES = ["europepmc", "pubmed", "psyarxiv", "socarxiv", "biorxiv_medrxiv", "openalex"]

# Display labels for picker
SOURCE_LABELS: Dict[str, str] = {
    "europepmc":       "Europe PMC",
    "pubmed":          "PubMed",
    "psyarxiv":        "PsyArXiv",
    "socarxiv":        "SocArXiv",
    "biorxiv_medrxiv": "bioRxiv / medRxiv",
    "openalex":        "OpenAlex",
}


def load_sources_config(path: str = "sources_config.yaml") -> Dict[str, Any]:
    """Load sources_config.yaml, falling back to defaults on any error."""
    config_path = Path(path)
    if not config_path.exists():
        logger.info("sources_config.yaml not found — using defaults")
        return _DEFAULT_CONFIG

    try:
        import yaml  # type: ignore
        with open(config_path) as f:
            loaded = yaml.safe_load(f) or {}
        # Merge loaded config over defaults so new keys always exist
        merged = dict(_DEFAULT_CONFIG)
        merged.update(loaded)
        merged["publication_sources"] = {**_DEFAULT_CONFIG["publication_sources"]}
        merged["publication_sources"].update(
            loaded.get("publication_sources", {})
        )
        return merged
    except Exception as e:
        logger.error(f"Failed to load sources_config.yaml: {e} — using defaults")
        return _DEFAULT_CONFIG


def get_enabled_search_sources(config: Dict[str, Any]) -> List[str]:
    """Return names of enabled search-capable sources (in priority order)."""
    sources = config.get("publication_sources", {})
    return [
        s for s in _SEARCH_SOURCES
        if sources.get(s, {}).get("enabled", False)
    ]


def get_default_selected_sources(config: Dict[str, Any]) -> List[str]:
    """Return names of sources selected by default in the source picker."""
    sources = config.get("publication_sources", {})
    return [
        s for s in _SEARCH_SOURCES
        if sources.get(s, {}).get("enabled", False)
           and sources.get(s, {}).get("default_selected", False)
    ]


def get_unpaywall_email(config: Dict[str, Any]) -> str:
    return config.get("unpaywall_email", "research@example.com")


def get_crossref_user_agent(config: Dict[str, Any]) -> str:
    return config.get("crossref_user_agent", "ResearchTool/1.0")


def is_source_enabled(config: Dict[str, Any], source: str) -> bool:
    return config.get("publication_sources", {}).get(source, {}).get("enabled", False)
