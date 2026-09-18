"""
Tests for per-user reference list routes.
Spec:    docs/web_parity_spec_2026-09-17.md#FP2-B
Tests:   tests/web/test_references_routes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import user_store

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
    from tests.web.conftest import ACCESS_CODE, account_body
    other_client.post("/api/session",
                      json=account_body(ACCESS_CODE))
    list_id = signed_in.post("/api/references", json={"name": "Private"}).json()["id"]

    # Other user's list endpoint sees no lists owned by signed_in
    other_lists = [lst["id"] for lst in other_client.get("/api/references").json()["lists"]]
    assert list_id not in other_lists

    # Delete attempt returns 404, not 403 (don't reveal existence)
    assert other_client.delete(f"/api/references/{list_id}").status_code == 404


def _add(signed_in, ctx, list_id, paper):
    """Put a paper in a list the way save-as-list does: store it, then link it.
    (There is no route for adding a client-supplied paper — see REF-3.)"""
    pid = ctx.db.insert_paper(paper) or ctx.db.find_paper(paper)["id"]
    item_id = user_store.add_reference_item(ctx.db, list_id, pid)
    return pid, item_id


# ── Items ─────────────────────────────────────────────────────────────────────

def test_add_and_list_item(signed_in, ctx):
    list_id = signed_in.post("/api/references", json={"name": "Papers"}).json()["id"]
    _add(signed_in, ctx, list_id, PAPER)
    items = signed_in.get(f"/api/references/{list_id}/items").json()["items"]
    assert len(items) == 1
    assert items[0]["paper"]["title"] == PAPER["title"]


def test_add_duplicate_paper_is_idempotent(signed_in, ctx):
    list_id = signed_in.post("/api/references", json={"name": "Dedup"}).json()["id"]
    _add(signed_in, ctx, list_id, PAPER)
    _add(signed_in, ctx, list_id, PAPER)
    items = signed_in.get(f"/api/references/{list_id}/items").json()["items"]
    assert len(items) == 1


def test_remove_item(signed_in, ctx):
    list_id = signed_in.post("/api/references", json={"name": "RemoveTest"}).json()["id"]
    _, item_id = _add(signed_in, ctx, list_id, PAPER)
    r = signed_in.delete(f"/api/references/{list_id}/items/{item_id}")
    assert r.status_code == 200
    assert signed_in.get(f"/api/references/{list_id}/items").json()["items"] == []


def test_ref1_delete_list_removes_its_item_rows(signed_in, ctx):
    """REF-1: the item rows are gone, not just unreachable. The schema's ON
    DELETE CASCADE never fires (foreign keys are off in SQLite by default), so
    the previous test — which only checked the list 404s — could not fail."""
    list_id = signed_in.post("/api/references", json={"name": "Cascade"}).json()["id"]
    _add(signed_in, ctx, list_id, PAPER)
    signed_in.delete(f"/api/references/{list_id}")
    count = ctx.db.conn.execute(
        "SELECT COUNT(*) FROM user_reference_list_items WHERE list_id = ?", (list_id,)
    ).fetchone()[0]
    assert count == 0


def test_ref1_delete_does_not_touch_another_users_items(signed_in, other_client, ctx):
    from tests.web.conftest import ACCESS_CODE, account_body
    list_id = signed_in.post("/api/references", json={"name": "Mine"}).json()["id"]
    _add(signed_in, ctx, list_id, PAPER)
    other_client.post("/api/session", json=account_body(ACCESS_CODE))
    assert other_client.delete(f"/api/references/{list_id}").status_code == 404
    assert len(signed_in.get(f"/api/references/{list_id}/items").json()["items"]) == 1


def test_ref2_duplicate_list_name_is_409(signed_in):
    assert signed_in.post("/api/references", json={"name": "Reading"}).status_code == 201
    r = signed_in.post("/api/references", json={"name": "Reading"})
    assert r.status_code == 409
    assert "Reading" in r.json()["detail"]


def test_ref2_failed_create_with_papers_leaves_nothing(signed_in, ctx):
    """A duplicate name aborts the whole transaction: no orphan items."""
    user_id = signed_in.get("/api/me").json()["user_id"]
    pid = ctx.db.insert_paper(PAPER)
    user_store.create_reference_list(ctx.db, user_id, "Taken")
    before = ctx.db.conn.execute("SELECT COUNT(*) FROM user_reference_list_items").fetchone()[0]
    with pytest.raises(user_store.DuplicateListName):
        user_store.create_reference_list_with_papers(ctx.db, user_id, "Taken", [pid])
    after = ctx.db.conn.execute("SELECT COUNT(*) FROM user_reference_list_items").fetchone()[0]
    assert after == before


def test_ref3_no_route_adds_a_client_supplied_paper(signed_in, app):
    """REF-3: a client-supplied paper dict would put any URL behind the PDF proxy."""
    list_id = signed_in.post("/api/references", json={"name": "NoAdd"}).json()["id"]
    r = signed_in.post(f"/api/references/{list_id}/items",
                       json={"paper": {**PAPER, "best_oa_url": "https://evil.example/x"}})
    assert r.status_code == 405
    paths = {(getattr(rt, "path", ""), m) for rt in app.routes for m in getattr(rt, "methods", [])}
    assert ("/api/references/{list_id}/items", "POST") not in paths


# ── CSV export ────────────────────────────────────────────────────────────────

def test_csv_export_has_correct_headers(signed_in, ctx):
    list_id = signed_in.post("/api/references", json={"name": "Export"}).json()["id"]
    _add(signed_in, ctx, list_id, PAPER)
    r = signed_in.get(f"/api/references/{list_id}/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.splitlines()
    assert lines[0] == '"Title","Authors","Date","DOI","Source","PDF URL"'


def test_csv_formula_injection_is_escaped(signed_in, ctx):
    """A title starting with = must be prefixed with ' to block spreadsheet formulas."""
    evil_paper = {**PAPER, "title": "=SUM(A1:Z99)", "doi": "10.1234/evil",
                  "canonical_id": "doi:10.1234/evil"}
    list_id = signed_in.post("/api/references", json={"name": "Evil"}).json()["id"]
    _add(signed_in, ctx, list_id, evil_paper)
    r = signed_in.get(f"/api/references/{list_id}/export.csv")
    assert "'=SUM" in r.text
    assert "\"=SUM" not in r.text          # no bare =SUM as start-of-field


def test_ref4_csv_uses_the_papers_biorxiv_version(signed_in, ctx):
    """REF-4: the export's SELECT omitted `version`, so every bioRxiv paper
    exported a v1 URL."""
    v3 = {**PAPER, "doi": "10.1101/2026.01.01.123456", "canonical_id": "doi:10.1101/2026.01.01.123456",
          "server": "biorxiv", "source": "biorxiv_medrxiv", "version": 3}
    list_id = signed_in.post("/api/references", json={"name": "Versions"}).json()["id"]
    _add(signed_in, ctx, list_id, v3)
    text = signed_in.get(f"/api/references/{list_id}/export.csv").text
    assert "v3.full.pdf" in text
    assert "v1.full.pdf" not in text


# ── PDF proxy ─────────────────────────────────────────────────────────────────

def _pdf_route(signed_in, ctx, name):
    list_id = signed_in.post("/api/references", json={"name": name}).json()["id"]
    pid, _ = _add(signed_in, ctx, list_id, PAPER)
    return f"/api/references/{list_id}/pdf/{pid}"


def test_ref6_http_pdf_link_is_tried_as_https(signed_in, monkeypatch, ctx):
    """Cold sweep: the summary path upgraded http links and the proxy did not.
    Both now use safe_fetch.https_candidate; the fetcher itself stays
    https-only (tests/web/test_safe_fetch.py::test_redirect_to_http_is_refused)."""
    import web.routes_references as rr
    route = _pdf_route(signed_in, ctx, "PDFHttp")
    monkeypatch.setattr(rr, "_pdf_url_from_paper", lambda p: "http://pub.example/x.pdf")
    asked = []

    def fake_fetch(url, *a, **k):
        asked.append(url)
        return b"%PDF-1.7 ok"
    monkeypatch.setattr(rr.safe_fetch, "fetch_pdf", fake_fetch)
    assert signed_in.get(route).status_code == 200
    assert asked == ["https://pub.example/x.pdf"]


def test_ref6_proxy_refuses_non_http_schemes(signed_in, monkeypatch, ctx):
    """Only http(s) is upgraded; anything else reaches the fetcher unchanged
    and is refused before any network use."""
    import web.routes_references as rr
    route = _pdf_route(signed_in, ctx, "PDFFile")
    monkeypatch.setattr(rr, "_pdf_url_from_paper", lambda p: "file:///etc/passwd")
    assert signed_in.get(route).status_code == 403


@pytest.mark.parametrize("exc,code", [
    ("FetchRefused", 403), ("NotAPdf", 422), ("TooLarge", 413), ("FetchFailed", 502)])
def test_ref5_pdf_proxy_maps_fetch_outcomes(signed_in, monkeypatch, ctx, exc, code):
    import web.routes_references as rr
    from src import safe_fetch
    route = _pdf_route(signed_in, ctx, f"Map{exc}")
    monkeypatch.setattr(rr, "_pdf_url_from_paper", lambda p: "https://pub.example/x.pdf")

    def boom(*a, **k):
        raise getattr(safe_fetch, exc)("detail")
    monkeypatch.setattr(rr.safe_fetch, "fetch_pdf", boom)
    assert signed_in.get(route).status_code == code


def test_ref5_pdf_proxy_returns_the_pdf(signed_in, monkeypatch, ctx):
    import web.routes_references as rr
    route = _pdf_route(signed_in, ctx, "PDFOk")
    monkeypatch.setattr(rr, "_pdf_url_from_paper", lambda p: "https://pub.example/x.pdf")
    monkeypatch.setattr(rr.safe_fetch, "fetch_pdf", lambda *a, **k: b"%PDF-1.7 ok")
    r = signed_in.get(route)
    assert r.status_code == 200
    assert r.content == b"%PDF-1.7 ok"
    assert r.headers["content-type"] == "application/pdf"


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
