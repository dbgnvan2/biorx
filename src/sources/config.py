"""
Sources configuration loader.
Reads sources_config.yaml and exposes feature-flag helpers.
"""

from pathlib import Path
from typing import Dict, Any, List
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG: Dict[str, Any] = {
    "publication_sources": {
        "europepmc":        {"enabled": True,  "default_selected": True},
        "pubmed":           {"enabled": True,  "default_selected": True},
        "psyarxiv":         {"enabled": True,  "default_selected": True},
        "socarxiv":         {"enabled": True,  "default_selected": True},
        "biorxiv_medrxiv":  {"enabled": True,  "default_selected": False},
        "arxiv":            {"enabled": True,  "default_selected": False},
        "openalex":         {"enabled": False, "default_selected": False},
        "crossref":         {"enabled": True},
        "unpaywall":        {"enabled": True},
        "pmc_oa":           {"enabled": False},
    },
    # The user's own address for the polite pools (Crossref, OpenAlex, arXiv)
    # and Unpaywall, which requires one. Empty by default: each user runs their
    # own copy, so a shipped address would speak for someone else.
    "contact_email": "",
}

CONTACT_EMAIL_ENV = "BIORX_CONTACT_EMAIL"
USER_AGENT_PRODUCT = "biorx/1.0"

# Sources that are search-capable (shown in picker)
_SEARCH_SOURCES = ["europepmc", "pubmed", "psyarxiv", "socarxiv", "biorxiv_medrxiv", "arxiv", "openalex"]

# Display labels for picker
SOURCE_LABELS: Dict[str, str] = {
    "europepmc":       "Europe PMC",
    "pubmed":          "PubMed",
    "psyarxiv":        "PsyArXiv",
    "socarxiv":        "SocArXiv",
    "biorxiv_medrxiv": "bioRxiv / medRxiv",
    "arxiv":           "arXiv",
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


def get_contact_email(config: Dict[str, Any]) -> str:
    """
    Purpose: The user's contact address: environment, then config, else empty.
    Spec:    docs/implementation_plan_2026-09-16_backlog.md#batch-h
    Tests:   tests/test_h_environment.py::test_h_contact_email_env_wins_over_config

    `unpaywall_email` is the key older copies of sources_config.yaml used; it is
    still read so an existing file keeps working. Never falls back to a
    placeholder address: an empty result lets callers say what is missing.
    """
    env = os.environ.get(CONTACT_EMAIL_ENV, "").strip()
    if env:
        return env
    for key in ("contact_email", "unpaywall_email"):
        value = str(config.get(key) or "").strip()
        if value:
            return value
    return ""


def polite_user_agent(config: Dict[str, Any]) -> str:
    """
    Purpose: User-Agent for API polite pools, with mailto only when an address is set.
    Spec:    docs/implementation_plan_2026-09-16_backlog.md#batch-h
    Tests:   tests/test_h_environment.py::test_h_user_agent_has_mailto_only_when_an_address_is_set
    """
    email = get_contact_email(config)
    return f"{USER_AGENT_PRODUCT} (mailto:{email})" if email else USER_AGENT_PRODUCT


def get_unpaywall_email(config: Dict[str, Any]) -> str:
    return get_contact_email(config)


def get_crossref_user_agent(config: Dict[str, Any]) -> str:
    return polite_user_agent(config)


def is_source_enabled(config: Dict[str, Any], source: str) -> bool:
    return config.get("publication_sources", {}).get(source, {}).get("enabled", False)
