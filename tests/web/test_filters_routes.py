"""
Tests for the saved-filter routes.

Spec: docs/implementation_plan_2026-09-15.md#2.2, W6
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from tests.web.conftest import ACCESS_CODE

FILTER = {
    "days_back": 14,
    "text_groups": [{"title": "", "abstract": "", "both": "generative agents"}],
    "authors": [],
}


def test_a_new_user_starts_with_the_shared_filters(signed_in):
    filters = signed_in.get("/api/filters").json()["filters"]
    assert filters, "a new user should be seeded from filters.json"
    assert all("name" in f for f in filters)


def test_create_read_update_delete(signed_in):
    created = signed_in.post("/api/filters",
                             json={"name": "Agents", "filter": FILTER}).json()
    assert created["name"] == "Agents"
    filter_id = created["id"]

    listed = signed_in.get("/api/filters").json()["filters"]
    assert any(f["id"] == filter_id for f in listed)

    updated = signed_in.put(f"/api/filters/{filter_id}",
                            json={"name": "Agents", "enabled": False,
                                  "filter": {**FILTER, "days_back": 30}}).json()
    assert updated["enabled"] is False
    assert updated["days_back"] == 30

    assert signed_in.delete(f"/api/filters/{filter_id}").status_code == 200
    assert not any(f["id"] == filter_id
                   for f in signed_in.get("/api/filters").json()["filters"])


def test_renaming_does_not_leave_a_duplicate(signed_in):
    created = signed_in.post("/api/filters",
                             json={"name": "Before", "filter": FILTER}).json()
    signed_in.put(f"/api/filters/{created['id']}",
                  json={"name": "After", "filter": FILTER})
    names = [f["name"] for f in signed_in.get("/api/filters").json()["filters"]]
    assert "After" in names
    assert "Before" not in names


def test_updating_a_missing_filter_is_404(signed_in):
    assert signed_in.put("/api/filters/99999",
                         json={"name": "x", "filter": FILTER}).status_code == 404


def test_deleting_a_missing_filter_is_404(signed_in):
    assert signed_in.delete("/api/filters/99999").status_code == 404


def test_one_user_cannot_see_or_delete_anothers_filter(app):
    alice = TestClient(app)
    if True:
        alice.post("/api/session", json={"access_code": ACCESS_CODE})
        created = alice.post("/api/filters",
                             json={"name": "Alice only", "filter": FILTER}).json()

    bob = TestClient(app)
    if True:
        bob.post("/api/session", json={"access_code": ACCESS_CODE})
        names = [f["name"] for f in bob.get("/api/filters").json()["filters"]]
        assert "Alice only" not in names
        assert bob.delete(f"/api/filters/{created['id']}").status_code == 404

    alice2 = TestClient(app)
    if True:
        pass


def test_an_empty_name_is_rejected(signed_in):
    assert signed_in.post("/api/filters",
                          json={"name": "", "filter": FILTER}).status_code == 422
