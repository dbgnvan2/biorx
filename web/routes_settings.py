"""
Purpose: Read and write allowed YAML config files via the web UI.
Spec:    docs/web_parity_spec_2026-09-17.md#FP3-B
Tests:   tests/web/test_settings_routes.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .auth import current_user, get_context
from .deps import AppContext

router = APIRouter()

# Only these filenames may be read or written. Never accept a path from the
# client directly — the allowlist prevents path traversal and limits blast radius.
_ALLOWED_FILES = {"sources_config.yaml", "llm_config.yaml"}

# Config files live next to the project root. Resolve relative to repo root:
# this file is web/routes_settings.py → parent.parent is the repo root.
_CONFIG_DIR = Path(__file__).parent.parent


def _validate_filename(filename: str) -> Path:
    """Return the resolved Path or raise 403.

    Rejects anything not in the allowlist, and independently rejects path
    components (/ or ..) even if the allowlist check were bypassed.
    """
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Invalid filename.")
    if filename not in _ALLOWED_FILES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"'{filename}' is not an editable config file.")
    return _CONFIG_DIR / filename


class SettingsSaveBody(BaseModel):
    content: str = Field(max_length=200_000)


@router.get("/api/settings/{filename}")
def get_settings(filename: str,
                 ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    """Return the raw YAML text of an allowed config file."""
    path = _validate_filename(filename)
    try:
        return {"filename": filename, "content": path.read_text()}
    except FileNotFoundError:
        return {"filename": filename, "content": ""}


@router.put("/api/settings/{filename}")
def put_settings(filename: str, body: SettingsSaveBody,
                 ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    """Validate YAML and overwrite the config file.

    Returns 422 on YAML parse error (before writing), 403 on path traversal or
    unknown filename. A successful write returns {"ok": true}.
    """
    path = _validate_filename(filename)
    try:
        yaml.safe_load(body.content)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid YAML: {exc}",
        ) from exc
    try:
        path.write_text(body.content)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not write {filename}: {exc}",
        ) from exc
    return {"ok": True, "filename": filename}
