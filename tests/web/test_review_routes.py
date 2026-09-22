"""
Tests for M4 — the cross-paper review routes.

Spec: docs/implementation_plan_2026-09-20_references_batch.md#M4
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import user_store
from src.tokens import UNCOUNTED, TokenUsage

PAPER = {
    "title": "Attachment and sleep",
    "authors": "Smith J",
    "abstract": "An abstract about attachment and sleep.",
    "pub_date": "2026-01-01",
    "doi": "10.1234/one",
    "canonical_id": "doi:10.1234/one",
    "source": "pubmed",
}
SECOND = dict(PAPER, title="Loneliness and inflammation", doi="10.1234/two",
              canonical_id="doi:10.1234/two",
              abstract="An abstract about loneliness.")

REVIEW_TEXT = "Both papers point to social regulation of physiology. [1] and [2] differ on mechanism."


_list_counter = iter(range(1, 1000))


def _list_with(signed_in, ctx, *papers, name=None):
    # Distinct names: list names are unique per user, so a helper that always
    # used the same one would 409 the moment a test made two lists.
    name = name or f"For review {next(_list_counter)}"
    created = signed_in.post("/api/references", json={"name": name})
    assert created.status_code == 201, created.text
    list_id = created.json()["id"]
    item_ids = []
    for paper in papers:
        pid = ctx.db.insert_paper(dict(paper))
        item_ids.append(user_store.add_reference_item(ctx.db, list_id, pid))
    return list_id, item_ids


def _llm(text=REVIEW_TEXT, usage=UNCOUNTED):
    client = MagicMock()
    client.generate.return_value = (text, usage)
    return client


def _await(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/reviews/{job_id}").json()
        if body["status"] in ("done", "error", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"review job never settled: {body}")


def _run(signed_in, ctx, list_id, client=None, item_ids=None):
    body = {"list_id": list_id, "api_key": "sk-inline", "provider": "anthropic"}
    if item_ids:
        body["item_ids"] = ",".join(str(i) for i in item_ids)
    with patch("src.llm_providers.build_client", return_value=client or _llm()):
        start = signed_in.post("/api/reviews", json=body)
        assert start.status_code == 202, start.text
        return _await(signed_in, start.json()["job_id"])


# ── M4.A.1: the synthesis, and what it read ───────────────────────────────────

def test_m4a1_a_review_runs_over_the_list(signed_in, ctx):
    list_id, _ = _list_with(signed_in, ctx, PAPER, SECOND)
    body = _run(signed_in, ctx, list_id)
    assert body["status"] == "done"
    result = body["result"]
    assert result["review_text"] == REVIEW_TEXT
    assert len(result["contributors"]) == 2


def test_m4a1_result_records_which_papers_contributed_and_on_what_basis(signed_in, ctx):
    """A synthesis that does not say what it read overstates its grounding."""
    list_id, _ = _list_with(signed_in, ctx, PAPER, SECOND)
    result = _run(signed_in, ctx, list_id)["result"]
    assert all(c["basis"] == "abstract" for c in result["contributors"])
    assert "abstracts only" in result["basis_note"]


def test_m4a1_a_summarized_paper_contributes_its_summary(signed_in, ctx):
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    paper_id = ctx.db.get_paper_by_doi(PAPER["doi"])["id"]
    ctx.db.insert_summary(paper_id, summary_text="", key_findings=["A finding"],
                          methodology="Methods", conclusions="Conclusions",
                          model_version="deepseek-flash", source_text="full_text")

    with patch("src.llm_providers.build_client", return_value=_llm()) as build:
        _run(signed_in, ctx, list_id, client=build.return_value)
    prompt = build.return_value.generate.call_args.args[0]
    assert "A finding" in prompt
    assert "(read from: full text)" in prompt


def test_m4a2_a_review_makes_no_network_call_of_its_own(signed_in, ctx):
    """M4.A.2: only stored text. The full-text finders must not be touched —
    that is what makes the cost knowable before the run."""
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    with patch("web.routes_summaries._download_pdf_text",
               side_effect=AssertionError("the review tried to fetch")):
        body = _run(signed_in, ctx, list_id)
    assert body["status"] == "done"


def test_m4a1_a_list_with_nothing_to_read_is_refused_without_calling_the_model(
        signed_in, ctx):
    """A review of nothing would be invention. Said plainly, and no model runs."""
    list_id, _ = _list_with(signed_in, ctx, dict(PAPER, abstract=""))
    client = _llm()
    body = _run(signed_in, ctx, list_id, client=client)
    assert body["status"] == "error"
    assert "nothing to review" in body["error"]
    client.generate.assert_not_called()


def test_m4a1_a_subset_reviews_only_the_ticked_papers(signed_in, ctx):
    list_id, item_ids = _list_with(signed_in, ctx, PAPER, SECOND)
    client = _llm()
    _run(signed_in, ctx, list_id, client=client, item_ids=[item_ids[0]])
    prompt = client.generate.call_args.args[0]
    assert PAPER["title"] in prompt
    assert SECOND["title"] not in prompt


# ── M4.A.3: stored, and private ───────────────────────────────────────────────

def test_m4a3_a_stored_review_is_returned_on_reload(signed_in, ctx):
    list_id, _ = _list_with(signed_in, ctx, PAPER, SECOND)
    _run(signed_in, ctx, list_id)

    stored = signed_in.get(f"/api/references/{list_id}/review")
    assert stored.status_code == 200
    body = stored.json()
    assert body["review_text"] == REVIEW_TEXT
    assert len(body["contributors"]) == 2
    assert body["basis_note"]


def test_m4a3_an_unreviewed_list_says_so(signed_in, ctx):
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    assert signed_in.get(f"/api/references/{list_id}/review").status_code == 404


def test_m4a3_reviews_are_per_user(app, signed_in, other_client, ctx):
    """Another account's review must be invisible, not merely unrendered."""
    from tests.web.conftest import ACCESS_CODE, account_body
    other_client.post("/api/session", json=account_body(ACCESS_CODE))
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    _run(signed_in, ctx, list_id)

    assert other_client.get(f"/api/references/{list_id}/review").status_code == 404
    assert other_client.post("/api/reviews",
                             json={"list_id": list_id}).status_code == 404


def test_m4a3_a_later_review_does_not_destroy_the_earlier_one(signed_in, ctx):
    """Reviews accumulate, so a synthesis can be compared with one made before
    more papers were summarized."""
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    _run(signed_in, ctx, list_id, client=_llm("First review"))
    _run(signed_in, ctx, list_id, client=_llm("Second review"))

    rows = ctx.db.conn.execute(
        "SELECT review_text FROM user_reviews ORDER BY id").fetchall()
    assert [r["review_text"] for r in rows] == ["First review", "Second review"]
    assert signed_in.get(
        f"/api/references/{list_id}/review").json()["review_text"] == "Second review"


# ── Spend: a review is billed like a summary ──────────────────────────────────

def test_m4_a_review_records_its_tokens(signed_in, ctx, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    spent = TokenUsage(prompt=5000, completion=700, total=5700, counted=True)

    with patch("src.llm_providers.build_client",
               return_value=_llm(usage=spent)):
        start = signed_in.post("/api/reviews", json={"list_id": list_id})
        assert _await(signed_in, start.json()["job_id"])["status"] == "done"

    rows = [dict(r) for r in ctx.db.conn.execute("SELECT * FROM usage_events")]
    assert len(rows) == 1, "the reserved slot is reused, not duplicated"
    assert rows[0]["prompt_tokens"] == 5000
    assert rows[0]["tokens_counted"] == 1


def test_m4_a_review_draws_on_the_same_daily_allowance_as_a_summary(
        signed_in, ctx, monkeypatch):
    """One allowance covers both, so a review cannot sidestep the cap by being
    a different kind of call."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "1")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    list_id, _ = _list_with(signed_in, ctx, PAPER)

    with patch("src.llm_providers.build_client", return_value=_llm()):
        first = signed_in.post("/api/reviews", json={"list_id": list_id})
        assert first.status_code == 202
        _await(signed_in, first.json()["job_id"])
        second = signed_in.post("/api/reviews", json={"list_id": list_id})
    assert second.status_code == 429


def test_m4_an_empty_reply_is_an_error_not_a_blank_review(signed_in, ctx):
    """A stored review of "" would show as a finished synthesis with nothing
    in it."""
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    body = _run(signed_in, ctx, list_id, client=_llm("   "))
    assert body["status"] == "error"
    assert ctx.db.conn.execute(
        "SELECT COUNT(*) AS n FROM user_reviews").fetchone()["n"] == 0


# ── The preview: what it would read, before spending ──────────────────────────

def test_regate1_a_preview_reserves_no_allowance_on_the_shared_key(
        signed_in, ctx, monkeypatch):
    """Gate 2026-09-21 finding 1 (HIGH). The preview used the RESERVING
    credential check only to read a model name, and threw the reservation away,
    so on the ordinary shared-key deployment every click on Review checked
    consumed a slot of the day's allowance before the user confirmed.

    This must run WITH an owner key set. The earlier test ran without one, so
    the check raised before reserving anything and its "no rows" assertion
    passed without ever exercising the path that leaked.
    """
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "3")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    user_id = signed_in.get("/api/me").json()["user_id"]
    list_id, _ = _list_with(signed_in, ctx, PAPER, SECOND)

    for _ in range(5):                       # more previews than the whole cap
        r = signed_in.get(f"/api/references/{list_id}/review-preview")
        assert r.status_code == 200

    assert ctx.db.conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events").fetchone()["n"] == 0
    assert user_store.owner_usage_today(ctx.db, user_id) == 0

    # And the allowance is intact: a real review still runs after them.
    with patch("src.llm_providers.build_client", return_value=_llm()):
        start = signed_in.post("/api/reviews", json={"list_id": list_id})
    assert start.status_code == 202, "previews ate the allowance a review needed"


def test_regate1_the_reserving_check_is_only_used_where_money_is_spent():
    """The class, not the instance. _resolve_for reserves an allowance slot, so
    it belongs only in a route that then submits a job that spends. Anything
    that only needs to know who would pay uses resolve_credentials, which
    reserves nothing. Scans every route module, so a new caller is caught."""
    import ast

    web = Path(__file__).parent.parent.parent / "web"
    offenders = []
    for module in sorted(web.glob("*.py")):
        tree = ast.parse(module.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            src = ast.get_source_segment(module.read_text(), fn) or ""
            if fn.name == "_resolve_for" or "_resolve_for(" not in src:
                continue
            if "ctx.jobs.submit(" not in src:
                offenders.append(f"{module.name}:{fn.name}")
    assert not offenders, (
        f"reserving credential check called where nothing is spent: {offenders}"
    )


def test_m5a2_the_preview_costs_nothing_and_calls_nothing(signed_in, ctx):
    list_id, _ = _list_with(signed_in, ctx, PAPER, SECOND)
    client = _llm()
    with patch("src.llm_providers.build_client", return_value=client):
        body = signed_in.get(f"/api/references/{list_id}/review-preview").json()
    client.generate.assert_not_called()
    assert body["papers"] == 2
    assert body["prompt_tokens"] > 0
    assert "abstracts only" in body["basis_note"]
    assert ctx.db.conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events").fetchone()["n"] == 0


def test_m5a2_the_preview_token_count_is_measured_not_guessed(signed_in, ctx):
    """M5.A.2: the review's prompt is built from stored text, so its size is
    known rather than estimated from a typical paper."""
    list_id, _ = _list_with(signed_in, ctx, PAPER)
    small = signed_in.get(f"/api/references/{list_id}/review-preview").json()

    big_id, _ = _list_with(signed_in, ctx,
                           dict(PAPER, doi="10.1234/big",
                                canonical_id="doi:10.1234/big",
                                abstract="x" * 8000))
    big = signed_in.get(f"/api/references/{big_id}/review-preview").json()
    assert big["prompt_tokens"] > small["prompt_tokens"] * 4


def test_m5a2_the_preview_names_papers_it_cannot_read(signed_in, ctx):
    list_id, _ = _list_with(signed_in, ctx, PAPER, dict(SECOND, abstract=""))
    body = signed_in.get(f"/api/references/{list_id}/review-preview").json()
    assert body["papers"] == 1
    assert len(body["left_out"]) == 1
    assert body["left_out"][0]["title"] == SECOND["title"]


def test_m4_review_routes_need_a_session(client):
    assert client.post("/api/reviews", json={"list_id": 1}).status_code == 401
    assert client.get("/api/references/1/review").status_code == 401
    assert client.get("/api/references/1/review-preview").status_code == 401
