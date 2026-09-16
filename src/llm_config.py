"""
Purpose: Load llm_config.yaml — provider dialects, model ids, budgets, caps.
Spec:    docs/implementation_plan_2026-09-15.md#2.1, W2
Tests:   tests/web/test_llm_config.py

Model ids and dialects are configuration, not source literals (standards L1,
L8). Env vars override the file so a deployment can change a model without a
rebuild.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "llm_config.yaml"

# Used only when llm_config.yaml is missing entirely, so the app still starts.
_FALLBACK: Dict[str, Any] = {
    "default_provider": "ollama",
    "providers": {
        "ollama": {
            "dialect": "ollama", "base_url": "http://localhost:11434",
            "model": "qwen:7b", "timeout": 120,
        },
    },
    "max_text_chars": 12000,
    "summary_daily_cap_per_user": 25,
}


@dataclass(frozen=True)
class ProviderConfig:
    """One provider's resolved settings."""
    name: str
    dialect: str
    model: str
    timeout: int
    base_url: str = ""
    api_key_env: str = ""

    @property
    def needs_key(self) -> bool:
        return bool(self.api_key_env)

    def owner_key(self) -> str:
        """The server owner's key for this provider, from the environment."""
        return os.environ.get(self.api_key_env, "").strip() if self.api_key_env else ""


def config_path() -> Path:
    """Where llm_config.yaml lives. BIORX_LLM_CONFIG overrides."""
    explicit = os.environ.get("BIORX_LLM_CONFIG")
    if explicit:
        return Path(explicit)
    return Path(__file__).parent.parent / CONFIG_FILENAME


def load_llm_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the config file, falling back to built-in defaults on any error."""
    p = Path(path) if path else config_path()
    if not p.exists():
        logger.warning("%s not found — using built-in defaults", p)
        return dict(_FALLBACK)
    try:
        import yaml
        loaded = yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        logger.error("Could not read %s: %s — using built-in defaults", p, e)
        return dict(_FALLBACK)

    merged = dict(_FALLBACK)
    merged.update(loaded)
    # Providers merge per-provider rather than wholesale, so a partial file does
    # not silently delete a provider the code expects.
    providers = dict(_FALLBACK["providers"])
    providers.update(loaded.get("providers", {}) or {})
    merged["providers"] = providers
    return merged


def default_provider(config: Dict[str, Any]) -> str:
    """The provider to use when a user has expressed no preference.

    LLM_PROVIDER wins over the file. An unknown name is a configuration error
    worth a loud log rather than a silent fallback to something cheaper or
    more expensive than the operator intended.
    """
    name = (os.environ.get("LLM_PROVIDER") or config.get("default_provider") or "").strip()
    if name and name not in config.get("providers", {}):
        logger.error(
            "LLM provider %r is not defined in the config (known: %s) — "
            "falling back to %r",
            name, ", ".join(sorted(config.get("providers", {}))),
            _FALLBACK["default_provider"],
        )
        return _FALLBACK["default_provider"]
    return name or _FALLBACK["default_provider"]


def provider_config(config: Dict[str, Any], name: str) -> Optional[ProviderConfig]:
    """Resolve one provider's settings, or None if it is not configured."""
    raw = (config.get("providers") or {}).get(name)
    if not raw:
        return None
    model_env = raw.get("model_env", "")
    model = (os.environ.get(model_env) or "").strip() if model_env else ""
    return ProviderConfig(
        name=name,
        dialect=raw.get("dialect", ""),
        model=model or raw.get("model", ""),
        timeout=int(raw.get("timeout", 120)),
        base_url=raw.get("base_url", ""),
        api_key_env=raw.get("api_key_env", ""),
    )


def max_text_chars(config: Dict[str, Any]) -> int:
    return int(config.get("max_text_chars", _FALLBACK["max_text_chars"]))


def summary_daily_cap(config: Dict[str, Any]) -> int:
    """Owner-key summaries allowed per user per day. SUMMARY_DAILY_CAP_PER_USER overrides."""
    env = os.environ.get("SUMMARY_DAILY_CAP_PER_USER")
    if env:
        try:
            return int(env)
        except ValueError:
            logger.warning("SUMMARY_DAILY_CAP_PER_USER is not an integer — using config")
    return int(config.get("summary_daily_cap_per_user",
                          _FALLBACK["summary_daily_cap_per_user"]))
