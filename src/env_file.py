"""
Purpose: Load the project's .env into the environment for desktop and CLI runs.
Spec:    docs/implementation_plan_2026-09-18_filter_run.md#R3 (review finding 3)
Tests:   tests/test_env_file.py

The default provider (DeepSeek) needs DEEPSEEK_API_KEY. The web app gets it from
its host's environment; the desktop app and the CLI agents were started without
.env ever being read, so the key had to be exported by hand. Values already in
the environment win, so a deploy or a shell export is never overridden.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


def load_project_env(path: Optional[Union[str, Path]] = None) -> bool:
    """Load `path` (default: the project's .env). Returns True if a file was read.

    Existing environment variables are not overridden. A missing file is not an
    error — the web deploy has none — but it is logged at INFO so a missing key
    later can be traced to it.
    """
    env_path = Path(path) if path is not None else PROJECT_ENV
    if not env_path.is_file():
        logger.info("No .env at %s — using the environment as it is", env_path)
        return False
    import os
    from dotenv import dotenv_values
    values = dotenv_values(env_path)
    applied, kept = [], []
    for name, value in values.items():
        if value is None:
            continue
        if name in os.environ:
            kept.append(name)
        else:
            os.environ[name] = value
            applied.append(name)
    # Names only, never values: enough to see where a setting came from.
    logger.info("Loaded from %s: %s", env_path, ", ".join(sorted(applied)) or "nothing")
    blank = sorted(n for n, v in values.items() if v is not None and not v.strip())
    if blank:
        logger.warning("Set but empty in %s: %s", env_path, ", ".join(blank))
    if kept:
        logger.info("Already set in the environment, so not taken from %s: %s",
                    env_path, ", ".join(sorted(kept)))
    return True
