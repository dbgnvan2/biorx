"""
Tests for the saved-filter routes.

Spec: docs/implementation_plan_2026-09-15.md#2.2, W6
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from tests.web.conftest import ACCESS_CODE, account_body

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


def test_a2_rename_keeps_the_id(signed_in):
    """A rename updates the filter in place (review A2). It used to be saved as
    a new row with the old one deleted, which is also how a rename onto another
    filter's name overwrote that filter."""
    created = signed_in.post("/api/filters",
                             json={"name": "sleep", "filter": FILTER}).json()
    renamed = signed_in.put(f"/api/filters/{created['id']}",
                            json={"name": "sleep, apnea", "filter": FILTER}).json()
    assert renamed["name"] == "sleep, apnea" and renamed["id"] == created["id"]
    again = signed_in.put(f"/api/filters/{created['id']}",
                          json={"name": "sleep, apnea, snoring", "filter": FILTER})
    assert again.status_code == 200


def test_a2_rename_onto_existing_is_409(signed_in):
    a = signed_in.post("/api/filters", json={"name": "A", "filter": FILTER}).json()
    b_filter = dict(FILTER, text_groups=[{"both": "only in B"}])
    b = signed_in.post("/api/filters", json={"name": "B", "filter": b_filter}).json()
    r = signed_in.put(f"/api/filters/{a['id']}", json={"name": "b", "filter": FILTER})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    by_id = {f["id"]: f for f in signed_in.get("/api/filters").json()["filters"]}
    assert by_id[a["id"]]["name"] == "A"
    assert by_id[b["id"]]["text_groups"] == [{"both": "only in B"}]


def test_a2_create_duplicate_is_409(signed_in):
    signed_in.post("/api/filters", json={"name": "Dup", "filter": FILTER})
    r = signed_in.post("/api/filters", json={"name": "  dup ", "filter": FILTER})
    assert r.status_code == 409
    assert [f["name"] for f in signed_in.get("/api/filters").json()["filters"]].count("Dup") == 1


def test_a2_case_only_rename_of_self_ok(signed_in):
    a = signed_in.post("/api/filters", json={"name": "stress", "filter": FILTER}).json()
    r = signed_in.put(f"/api/filters/{a['id']}", json={"name": "Stress", "filter": FILTER})
    assert r.status_code == 200 and r.json()["name"] == "Stress"


def test_updating_a_missing_filter_is_404(signed_in):
    assert signed_in.put("/api/filters/99999",
                         json={"name": "x", "filter": FILTER}).status_code == 404


def test_deleting_a_missing_filter_is_404(signed_in):
    assert signed_in.delete("/api/filters/99999").status_code == 404


def test_one_user_cannot_see_or_delete_anothers_filter(app):
    alice = TestClient(app)
    if True:
        alice.post("/api/session", json=account_body(ACCESS_CODE))
        created = alice.post("/api/filters",
                             json={"name": "Alice only", "filter": FILTER}).json()

    bob = TestClient(app)
    if True:
        bob.post("/api/session", json=account_body(ACCESS_CODE))
        names = [f["name"] for f in bob.get("/api/filters").json()["filters"]]
        assert "Alice only" not in names
        assert bob.delete(f"/api/filters/{created['id']}").status_code == 404

    alice2 = TestClient(app)
    if True:
        pass


def test_an_empty_name_is_rejected(signed_in):
    assert signed_in.post("/api/filters",
                          json={"name": "", "filter": FILTER}).status_code == 422


# ── LF: filters in the earlier web build's shape (2026-09-17 re-sweep) ─────────

LEGACY = {"text_groups": [{"keywords": "zebrafish"}], "date_from": "2020-01-01",
          "date_to": "2020-12-31", "institution": ["Harvard"]}


def test_lf1_legacy_filter_is_served_in_canonical_shape(signed_in):
    # Institution is refused on save since review B5 (no source reports it);
    # the legacy list form is covered by test_b5_stored_institution_is_ignored.
    legacy = {k: v for k, v in LEGACY.items() if k != "institution"}
    fid = signed_in.post("/api/filters", json={"name": "Old", "filter": legacy}).json()["id"]
    f = next(x for x in signed_in.get("/api/filters").json()["filters"] if x["id"] == fid)
    assert f["text_groups"] == [{"both": "zebrafish"}]
    assert (f["start_date"], f["end_date"]) == ("2020-01-01", "2020-12-31")
    assert "date_from" not in f


def test_lf2_legacy_filter_runs_filtered_not_open():
    """Run path, not the editor: the stored dict goes straight to the server."""
    from src.filtering import filter_papers, normalise_filter
    from src.sources.query_builder import build_europepmc_query
    cortisol = {"title": "Cortisol in humans", "abstract": "", "author_corresponding_institution": "Harvard"}
    assert filter_papers([cortisol], LEGACY) == []          # adversarial: was returned
    q = build_europepmc_query(normalise_filter(LEGACY))
    assert "zebrafish" in q and "2020-01-01" in q and "2020-12-31" in q


def test_lf2_run_search_normalises_before_querying(ctx):
    """_run_search hands the query builders the canonical shape."""
    from unittest.mock import MagicMock
    from web.routes_searches import _run_search
    seen = {}
    orch = MagicMock()
    orch.search = lambda filter_dict, **_: seen.setdefault("fd", filter_dict)
    ctx.get_orchestrator = lambda: orch
    work = _run_search(ctx, LEGACY, {"all": True, "selected": []}, 10)
    work(MagicMock(sources_failed=[]))
    assert seen["fd"]["text_groups"] == [{"both": "zebrafish"}]
    assert seen["fd"]["start_date"] == "2020-01-01"


# ── S3 / B5: facet values the search cannot apply are refused on save ────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#S3, #B5

@pytest.mark.parametrize("facet,value", [
    ("paper_type", "editorial"),
    ("license", "cc_by_4"),
    ("species", "Plants only"),
    ("version", "3"),
])
def test_s3_unknown_facet_value_refused(signed_in, facet, value):
    body = {"name": "Bad facet", "filter": dict(FILTER, **{facet: value})}
    r = signed_in.post("/api/filters", json=body)
    assert r.status_code == 400
    assert facet in r.json()["detail"]
    fid = signed_in.post("/api/filters", json={"name": "Good", "filter": FILTER}).json()["id"]
    assert signed_in.put(f"/api/filters/{fid}", json=dict(body, name="Good")).status_code == 400


def test_s3_legacy_labels_and_ids_both_accepted(signed_in):
    """The current page sends display labels; the new one sends ids."""
    for extra in ({"paper_type": "review article", "license": "cc_by"},
                  {"paper_type": "review", "license": "cc-by"}):
        r = signed_in.post("/api/filters", json={"name": str(extra), "filter": dict(FILTER, **extra)})
        assert r.status_code == 201, r.json()
        assert (r.json()["paper_type"], r.json()["license"]) == ("review", "cc-by")


def test_b5_institution_refused(signed_in):
    r = signed_in.post("/api/filters", json={
        "name": "Inst", "filter": dict(FILTER, institution="Harvard")})
    assert r.status_code == 400
    assert "institution" in r.json()["detail"]


def test_s3_vocabulary_served(signed_in):
    from src import filter_vocabulary
    body = signed_in.get("/api/vocabulary").json()
    assert body == filter_vocabulary.for_client()
    assert {"id": "no-animal", "label": "Exclude animal studies"} in body["species"]
