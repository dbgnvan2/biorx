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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "llm_config.yaml"

# Used only when llm_config.yaml is missing entirely, so the app still starts.
# Ollama because it is the one provider that needs no key; the real default
# (deepseek) lives in llm_config.yaml. Model = the one installed here.
_FALLBACK: Dict[str, Any] = {
    "default_provider": "ollama",
    "providers": {
        "ollama": {
            "dialect": "ollama", "base_url": "http://localhost:11434",
            "model": "qwen3.5:4b", "timeout": 120,
        },
    },
    "max_text_chars": 12000,
    "summary_daily_cap_per_user": 25,
    "not_summarizable_title_prefixes": [],
    "discover": {"days_back": 90, "max_papers": 30, "stop_words": []},
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
    api_key: str = ""      # key stored directly in the config file (desktop use)
    # Provider-side reasoning ("thinking") setting sent with each request, e.g.
    # "disabled" for DeepSeek, whose deepseek-flash thinks by default at high
    # effort (billed as extra output). Empty means send nothing.
    thinking: str = ""

    @property
    def needs_key(self) -> bool:
        return bool(self.api_key_env)

    def owner_key(self) -> str:
        """The server owner's key for this provider.

        Precedence: env var (set at launch) → api_key field in the config file
        (legacy; llm_config.yaml is committed to git, so prefer the env var).
        """
        if self.api_key_env:
            env_val = os.environ.get(self.api_key_env, "").strip()
            if env_val:
                return env_val
        return self.api_key.strip()


def owner_key_problems(config: Dict[str, Any]) -> List[str]:
    """Plain-language warnings about owner keys that look malformed (K2).

    Checks shape only and never includes the key: a key pasted twice (two
    identical halves) or carrying spaces or quotes is rejected by the provider
    with a message that does not say why. Found 2026-09-18: a doubled Anthropic
    key in .env overrode a good one from the shell.
    """
    problems = []
    for name in sorted((config or {}).get("providers", {})):
        pconf = provider_config(config, name)
        if not pconf or not pconf.needs_key:
            continue
        key = pconf.owner_key()
        if not key:
            continue
        label = pconf.api_key_env or f"{name} api_key"
        half = len(key) // 2
        if len(key) >= 20 and len(key) % 2 == 0 and key[:half] == key[half:]:
            problems.append(f"{label} looks pasted twice (its two halves are identical) — "
                            f"{name} will reject it.")
        elif any(c.isspace() or c in "\"'" for c in key):
            problems.append(f"{label} contains spaces or quotes — {name} will likely reject it.")
    return problems


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
    env_name = (os.environ.get("LLM_PROVIDER") or "").strip()
    name = env_name or (config.get("default_provider") or "").strip()
    if name and name not in config.get("providers", {}):
        logger.error(
            "LLM provider %r is not defined in the config (known: %s) — "
            "falling back to %r",
            name, ", ".join(sorted(config.get("providers", {}))),
            _FALLBACK["default_provider"],
        )
        return _FALLBACK["default_provider"]
    return name or _FALLBACK["default_provider"]


def default_provider_source(config: Dict[str, Any]) -> str:
    """Where the default provider comes from, for the start-up log: the
    LLM_PROVIDER variable silently outranks llm_config.yaml, which made a
    stray LLM_PROVIDER=anthropic in .env hard to find (2026-09-18)."""
    if (os.environ.get("LLM_PROVIDER") or "").strip():
        return "the LLM_PROVIDER environment variable (overrides llm_config.yaml)"
    return "default_provider in llm_config.yaml"


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
        api_key=raw.get("api_key", ""),
        thinking=str(raw.get("thinking", "") or "").strip(),
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


def non_article_kind(config: Dict[str, Any], title: str) -> str:
    """The notice prefix a title starts with ("correction to", …), or "".

    Spec:  docs/implementation_plan_2026-09-16_backlog.md#N2
    A heuristic over editorial config, used only to explain why a paper with
    no text cannot be summarized — never to refuse one that has text.
    """
    lowered = (title or "").strip().lower()
    for prefix in config.get("not_summarizable_title_prefixes", []) or []:
        prefix = str(prefix).strip().lower()
        if prefix and lowered.startswith(prefix):
            return prefix
    return ""
