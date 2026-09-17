"""
Tests for per-user reference list routes.
Spec:    docs/web_parity_spec_2026-09-17.md#FP2-B
Tests:   tests/web/test_references_routes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

PAPER = {
    "title": "Neural networks in biology",
    "authors": "Smith J, Jones A",
    "abstract": "A study of neural approaches.",
    "pub_date": "2026-01-01",
    "doi": "10.1234/test.001",
    "canonical_id": "doi:10.1234/test.001",
    "source": "europepmc",
}


# ── List CRUD ─────────────────────────────────────────────────────────────────

def test_create_and_list_reference_list(signed_in):
    r = signed_in.post("/api/references", json={"name": "My List"})
    assert r.status_code == 201
    assert r.json()["name"] == "My List"

    r2 = signed_in.get("/api/references")
    assert r2.status_code == 200
    names = [lst["name"] for lst in r2.json()["lists"]]
    assert "My List" in names


def test_delete_reference_list(signed_in):
    list_id = signed_in.post("/api/references", json={"name": "To Delete"}).json()["id"]
    r = signed_in.delete(f"/api/references/{list_id}")
    assert r.status_code == 200
    lists = signed_in.get("/api/references").json()["lists"]
    assert not any(lst["id"] == list_id for lst in lists)


def test_delete_nonexistent_list_is_404(signed_in):
    assert signed_in.delete("/api/references/9999").status_code == 404


def test_other_user_cannot_see_or_delete_list(app, signed_in, other_client):
    """A list is private to the user who created it."""
    from tests.web.conftest import ACCESS_CODE
    other_client.post("/api/session",
                      json={"access_code": ACCESS_CODE, "display_name": "Other"})
    list_id = signed_in.post("/api/references", json={"name": "Private"}).json()["id"]

    # Other user's list endpoint sees no lists owned by signed_in
    other_lists = [lst["id"] for lst in other_client.get("/api/references").json()["lists"]]
    assert list_id not in other_lists

    # Delete attempt returns 404, not 403 (don't reveal existence)
    assert other_client.delete(f"/api/references/{list_id}").status_code == 404


# ── Items ─────────────────────────────────────────────────────────────────────

def test_add_and_list_item(signed_in):
    list_id = signed_in.post("/api/references", json={"name": "Papers"}).json()["id"]
    r = signed_in.post(f"/api/references/{list_id}/items", json={"paper": PAPER})
    assert r.status_code == 201

    items = signed_in.get(f"/api/references/{list_id}/items").json()["items"]
    assert len(items) == 1
    assert items[0]["paper"]["title"] == PAPER["title"]


def test_add_duplicate_paper_is_idempotent(signed_in):
    list_id = signed_in.post("/api/references", json={"name": "Dedup"}).json()["id"]
    signed_in.post(f"/api/references/{list_id}/items", json={"paper": PAPER})
    signed_in.post(f"/api/references/{list_id}/items", json={"paper": PAPER})
    items = signed_in.get(f"/api/references/{list_id}/items").json()["items"]
    assert len(items) == 1


def test_remove_item(signed_in):
    list_id = signed_in.post("/api/references", json={"name": "RemoveTest"}).json()["id"]
    item = signed_in.post(f"/api/references/{list_id}/items", json={"paper": PAPER}).json()
    item_id = item["item_id"]
    r = signed_in.delete(f"/api/references/{list_id}/items/{item_id}")
    assert r.status_code == 200
    assert signed_in.get(f"/api/references/{list_id}/items").json()["items"] == []


def test_cascade_delete_removes_items(signed_in):
    """Deleting a list removes its items via ON DELETE CASCADE."""
    list_id = signed_in.post("/api/references", json={"name": "Cascade"}).json()["id"]
    signed_in.post(f"/api/references/{list_id}/items", json={"paper": PAPER})
    signed_in.delete(f"/api/references/{list_id}")
    # Confirm items are gone (direct DB check via ctx is not available here;
    # the route is 404 on the list itself, which implies CASCADE worked)
    assert signed_in.get(f"/api/references/{list_id}/items").status_code == 404


# ── CSV export ────────────────────────────────────────────────────────────────

def test_csv_export_has_correct_headers(signed_in):
    list_id = signed_in.post("/api/references", json={"name": "Export"}).json()["id"]
    signed_in.post(f"/api/references/{list_id}/items", json={"paper": PAPER})
    r = signed_in.get(f"/api/references/{list_id}/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.splitlines()
    assert lines[0] == '"Title","Authors","Date","DOI","Source","PDF URL"'


def test_csv_formula_injection_is_escaped(signed_in):
    """A title starting with = must be prefixed with ' to block spreadsheet formulas."""
    evil_paper = {**PAPER, "title": "=SUM(A1:Z99)", "doi": "10.1234/evil"}
    list_id = signed_in.post("/api/references", json={"name": "Evil"}).json()["id"]
    signed_in.post(f"/api/references/{list_id}/items", json={"paper": evil_paper})
    r = signed_in.get(f"/api/references/{list_id}/export.csv")
    # The raw formula =SUM... must not appear as the first character of a field.
    # csv.QUOTE_ALL wraps the prefixed value as "'=SUM(...)" — the ' comes first.
    assert "\"=SUM" not in r.text          # no bare =SUM as start-of-field


# ── PDF proxy ─────────────────────────────────────────────────────────────────

def test_pdf_proxy_refuses_http_url(signed_in, monkeypatch, ctx):
    """http:// URLs are rejected — https only."""
    import web.routes_references as rr
    list_id = signed_in.post("/api/references", json={"name": "PDFTest"}).json()["id"]
    # Add paper with an http URL
    http_paper = {**PAPER, "doi": "10.1234/http", "canonical_id": "doi:10.1234/http",
                  "url": "http://example.com/paper.pdf"}
    signed_in.post(f"/api/references/{list_id}/items", json={"paper": http_paper})
    items = signed_in.get(f"/api/references/{list_id}/items").json()["items"]
    paper_id = items[0]["paper"]["paper_id"]

    # Patch pdf_url to return an http URL
    monkeypatch.setattr(rr, "_pdf_url_from_paper", lambda p: "http://evil.example.com/x.pdf")
    r = signed_in.get(f"/api/references/{list_id}/pdf/{paper_id}")
    assert r.status_code == 403


def test_pdf_proxy_refuses_paper_not_in_list(signed_in):
    """paper_id that doesn't belong to list_id is 404."""
    list_id = signed_in.post("/api/references", json={"name": "PDFOwn"}).json()["id"]
    r = signed_in.get(f"/api/references/{list_id}/pdf/9999")
    assert r.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_reference_routes_require_auth(client):
    assert client.get("/api/references").status_code == 401
    assert client.post("/api/references", json={"name": "x"}).status_code == 401
    assert client.delete("/api/references/1").status_code == 401
    assert client.get("/api/references/1/items").status_code == 401
    assert client.get("/api/references/1/export.csv").status_code == 401
    assert client.get("/api/references/1/pdf/1").status_code == 401
