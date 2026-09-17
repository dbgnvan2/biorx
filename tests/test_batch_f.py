"""
Batch F — shared resolver and writable-dir guard for SearchCache (P5/P28/P10).

F1: SearchCache path resolution — DATA_DIR takes precedence over ~/preprints,
    and BIORX_CACHE_PATH takes precedence over DATA_DIR (P5 sibling contract).

F2: Mutation-proof companion — proves the precedence guard would catch a
    resolver that ignores DATA_DIR (P27 — the assertion in F1 has no explicit
    inverse, so a companion is required).
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.sources.cache import SearchCache, _default_cache_path  # noqa: E402


def test_f1_search_cache_data_dir_wins_over_default(tmp_path, monkeypatch):
    """
    SearchCache respects DATA_DIR when BIORX_CACHE_PATH is unset.

    Constructs SearchCache() with DATA_DIR set and no explicit BIORX_CACHE_PATH,
    then asserts the db lands under tmp_path — not ~/preprints.

    Mutation proof: remove the DATA_DIR branch from resolve_data_path → the path
    resolves to ~/preprints and the assertion fails with "expected tmp, got ~/preprints".
    """
    monkeypatch.delenv("BIORX_CACHE_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    cache = SearchCache()
    try:
        assert cache.path.parent == tmp_path, (
            f"SearchCache path {cache.path} did not land under DATA_DIR={tmp_path}"
        )
        assert cache.path.name == "source_cache.db"
    finally:
        cache.close()


def test_f1_biorx_cache_path_wins_over_data_dir(tmp_path, monkeypatch):
    """
    BIORX_CACHE_PATH takes precedence over DATA_DIR (P5 — both siblings obey the
    same env-key priority: explicit key > DATA_DIR > default).

    Mutation proof: swap the branch order in resolve_data_path → BIORX_CACHE_PATH
    is ignored and the assertion fails with "expected explicit path, got DATA_DIR".
    """
    explicit = tmp_path / "explicit" / "cache.db"
    monkeypatch.setenv("BIORX_CACHE_PATH", str(explicit))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data_dir"))

    resolved = _default_cache_path()
    assert resolved == str(explicit), (
        f"BIORX_CACHE_PATH should win over DATA_DIR; got {resolved}"
    )


def test_f2_no_data_dir_resolves_to_default(monkeypatch):
    """
    Companion: without DATA_DIR or BIORX_CACHE_PATH, the resolver falls through
    to the DEFAULT_DATA_DIR constant (proves F1's DATA_DIR branch is the non-default).
    """
    from src.db import DEFAULT_DATA_DIR

    monkeypatch.delenv("BIORX_CACHE_PATH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    resolved = _default_cache_path()
    assert resolved == f"{DEFAULT_DATA_DIR}/source_cache.db", (
        f"Without overrides, expected default path; got {resolved}"
    )
