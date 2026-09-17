"""
Tests for filter test and save-as-list routes.
Spec:    docs/web_parity_spec_2026-09-17.md#FP1-B, FP4-B
Tests:   tests/web/test_filter_test_route.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

PAPER = {
    "title": "Test Paper Alpha",
    "authors": "Doe J",
    "abstract": "An abstract.",
    "pub_date": "2026-01-01",
    "doi": "10.1234/fp.001",
    "canonical_id": "doi:10.1234/fp.001",
    "source": "europepmc",
}


def _make_filter(signed_in):
    """Create a filter and return its id."""
    r = signed_in.post("/api/filters", json={
        "name": "TestFilter",
        "enabled": True,
        "filter": {"keywords": "biology"},
    })
    assert r.status_code == 201
    return r.json()["id"]


# ── Filter test ───────────────────────────────────────────────────────────────

def test_filter_test_returns_job_id(signed_in, monkeypatch):
    """POST /api/filters/{id}/test returns a job dict with an id."""
    from unittest.mock import patch, MagicMock

    filter_id = _make_filter(signed_in)

    # Stub out the orchestrator search so no network call is made
    with patch("web.routes_searches.AppContext.get_orchestrator") as mock_orch:
        mock_orch.return_value = MagicMock()
        mock_orch.return_value.search = lambda **_: None
        r = signed_in.post(f"/api/filters/{filter_id}/test")

    assert r.status_code == 202
    assert "job_id" in r.json()


def test_filter_test_nonexistent_filter_is_404(signed_in):
    assert signed_in.post("/api/filters/9999/test").status_code == 404


# ── Save as list ──────────────────────────────────────────────────────────────

def test_save_completed_search_as_list(signed_in, ctx):
    """A finished search job can be saved as a reference list.

    Injects a job result directly via the job registry to avoid constructing
    a full CanonicalRecord, which has many required fields.
    """
    # Submit a search with a stubbed orchestrator that produces no results.
    from unittest.mock import patch, MagicMock

    with patch.object(ctx, "get_orchestrator") as mo:
        mo.return_value = MagicMock()
        mo.return_value.get_enabled_sources.return_value = []
        mo.return_value.search = lambda **_: None
        job_r = signed_in.post("/api/searches", json={
            "filter": {"keywords": "test"},
            "source_selection": {"all": True, "selected": []},
        })
    assert job_r.status_code == 202
    job_id = job_r.json()["job_id"]

    # Wait for job to finish
    for _ in range(20):
        status = signed_in.get(f"/api/searches/{job_id}").json()["status"]
        if status == "done":
            break
        time.sleep(0.1)
    assert status == "done"

    # Save the (empty) result set as a reference list
    r = signed_in.post(f"/api/searches/{job_id}/save-as-list",
                       json={"name": "Saved From Search"})
    assert r.status_code == 201
    assert r.json()["name"] == "Saved From Search"

    # Verify it appears in references
    lists = signed_in.get("/api/references").json()["lists"]
    assert any(lst["name"] == "Saved From Search" for lst in lists)


def test_save_as_list_requires_auth(client):
    assert client.post("/api/searches/abc/save-as-list",
                       json={"name": "x"}).status_code == 401
