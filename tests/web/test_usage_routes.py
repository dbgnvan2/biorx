"""
Tests for M1.C.1 — GET /api/usage/session.

Spec: docs/implementation_plan_2026-09-20_references_batch.md#M1.C.1
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import user_store
from src.tokens import TokenUsage

COUNTED = TokenUsage(prompt=1000, completion=100, total=1100, counted=True)


def _spend(ctx, user_id, usage=COUNTED, key_source="user"):
    user_store.record_usage(ctx.db, user_id, "summary", "deepseek",
                            "deepseek-flash", key_source, usage)


def test_m1c1_session_usage_requires_a_signed_in_user(client):
    """The meter reports one user's spending; it is not public (S3)."""
    assert client.get("/api/usage/session").status_code == 401


def test_m1c1_a_fresh_session_reports_zero(signed_in):
    body = signed_in.get("/api/usage/session").json()
    assert body["total"] == 0
    assert body["counted_calls"] == 0 and body["uncounted_calls"] == 0
    assert body["session_start"] is not None


def test_m1c1_spending_shows_up(signed_in, ctx):
    user_id = signed_in.get("/api/me").json()["user_id"]
    _spend(ctx, user_id)
    _spend(ctx, user_id)

    body = signed_in.get("/api/usage/session").json()
    assert body["prompt"] == 2000
    assert body["completion"] == 200
    assert body["total"] == 2200
    assert body["counted_calls"] == 2


def test_m1c1_another_users_tokens_are_never_included(signed_in, ctx):
    """The decisive authorization check: one person's meter must never carry
    another's spending, however the rows sit in the shared table."""
    user_id = signed_in.get("/api/me").json()["user_id"]
    _spend(ctx, user_id)
    _spend(ctx, "somebody-else", TokenUsage(prompt=999999, completion=1,
                                            total=1000000, counted=True))

    body = signed_in.get("/api/usage/session").json()
    assert body["total"] == 1100, "another account's spending leaked into the meter"


def test_m1c1_session_total_excludes_earlier_sessions(signed_in, ctx):
    """"This session" means since this sign-in. Spending from before the cookie
    was issued belongs to a previous session and must not be counted, or the
    number silently becomes a lifetime total."""
    user_id = signed_in.get("/api/me").json()["user_id"]
    _spend(ctx, user_id)
    # Backdate one call to before this session began.
    ctx.db.conn.execute(
        "UPDATE usage_events SET created_at = ? WHERE user_id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=2))
         .strftime("%Y-%m-%d %H:%M:%S"), user_id))
    ctx.db.conn.commit()
    _spend(ctx, user_id)

    body = signed_in.get("/api/usage/session").json()
    assert body["total"] == 1100, "an earlier session's spending is still counted"
    assert body["counted_calls"] == 1


def test_m1c1_uncounted_calls_are_reported_separately(signed_in, ctx):
    """A provider that reported nothing is a real call with an unknown cost.
    It appears in its own tally so the page can say so, rather than being
    folded into the total as zero and reading as complete (P2)."""
    from src.tokens import UNCOUNTED

    user_id = signed_in.get("/api/me").json()["user_id"]
    _spend(ctx, user_id)
    _spend(ctx, user_id, UNCOUNTED)

    body = signed_in.get("/api/usage/session").json()
    assert body["total"] == 1100
    assert body["counted_calls"] == 1
    assert body["uncounted_calls"] == 1


def _found_full_text(ctx, paper, outcome=None, by_title=None):
    """Stand-in for a found PDF, so the model actually runs."""
    if outcome is not None:
        outcome.update(full_text="used", text_source="Unpaywall")
    return "Full text of the paper: methods, results and discussion."


def test_m1c1_a_real_summary_run_reaches_the_meter(signed_in, ctx, monkeypatch):
    """End to end, through the code path that actually spends: the route, the
    recorder and the meter agree. The pieces were tested apart; this is the one
    that fails if they stop lining up."""
    from unittest.mock import MagicMock, patch

    from tests.web.test_summaries_routes import PAPER, _await

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    client = MagicMock()
    client.summarize_paper.return_value = (
        {"key_findings": ["f"], "methodology": "m", "conclusions": "c"},
        TokenUsage(prompt=8000, completion=300, total=8300, counted=True))
    with patch("src.llm_providers.build_client", return_value=client), \
         patch("web.routes_summaries._extract_text", side_effect=_found_full_text):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        assert _await(signed_in, job_id)["status"] == "done"

    body = signed_in.get("/api/usage/session").json()
    assert body["prompt"] == 8000 and body["completion"] == 300
    assert body["counted_calls"] == 1


def test_m1c1_an_unreadable_cookie_reports_zero_not_a_lifetime_total(signed_in, ctx,
                                                                    monkeypatch):
    """If the issue time cannot be read, the honest answer is an empty window.
    Falling back to "since the beginning" would show this user a total built
    from sessions that are not this one."""
    user_id = signed_in.get("/api/me").json()["user_id"]
    _spend(ctx, user_id)

    monkeypatch.setattr("web.routes_usage.session_started_at", lambda *_: None)
    body = signed_in.get("/api/usage/session").json()
    assert body["total"] == 0
    assert body["session_start"] is None


# ── M5: the estimate endpoint ─────────────────────────────────────────────────

def test_m5a3_estimate_requires_a_signed_in_user(client):
    assert client.post("/api/usage/estimate",
                       json={"papers": 3}).status_code == 401


def test_m5a3_estimate_reports_a_range_and_who_pays(signed_in, ctx, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "25")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    body = signed_in.post("/api/usage/estimate", json={"papers": 8}).json()
    assert body["papers"] == 8
    assert body["low"] < body["high"], "an estimate must be a range"
    assert body["exact"] is False
    assert body["billed_to_owner"] is True
    assert body["cap_remaining"] == 25


def test_m5a3_the_cap_remaining_falls_as_it_is_used(signed_in, ctx, monkeypatch):
    """The dialog's allowance figure must reflect what has already been spent
    today, or it promises room that is not there."""
    from src import user_store

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "5")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    user_id = signed_in.get("/api/me").json()["user_id"]

    user_store.reserve_owner_usage(ctx.db, user_id, "summary", 5, "deepseek", "m")
    user_store.reserve_owner_usage(ctx.db, user_id, "summary", 5, "deepseek", "m")

    body = signed_in.post("/api/usage/estimate", json={"papers": 8}).json()
    assert body["cap_remaining"] == 3


def test_m5a3_a_user_on_their_own_key_is_not_capped(signed_in, ctx, monkeypatch,
                                                    enc_secret):
    """A user paying for their own calls has no allowance to report."""
    from src import user_store

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    user_id = signed_in.get("/api/me").json()["user_id"]
    user_store.set_llm_key(ctx.db, user_id, "deepseek", "sk-their-own-key")

    body = signed_in.post("/api/usage/estimate", json={"papers": 8}).json()
    assert body["billed_to_owner"] is False
    assert body["cap_remaining"] is None
    assert body["key_source"] == "user"


def test_m5a1_zero_papers_estimates_nothing(signed_in, ctx, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    body = signed_in.post("/api/usage/estimate", json={"papers": 0}).json()
    assert body["low"] == 0 and body["high"] == 0


def test_gate1_a_localstorage_only_key_is_reported_as_the_users_own(
        signed_in, ctx, monkeypatch):
    """Gate 2026-09-21 finding 1. A key held only in the browser is a supported
    mode, and the spend path bills it — but the estimate resolved credentials
    with its own copy of the logic that only looked at the server-stored key,
    so the dialog said "billed to the shared key", showed an allowance that did
    not apply, and could refuse a run the user's own key would have paid for.

    A spend dialog that names the wrong payer is worse than no dialog.
    """
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    body = signed_in.post("/api/usage/estimate", json={
        "papers": 5, "api_key": "sk-in-the-browser-only",
        "provider": "anthropic", "model": "claude-sonnet-5"}).json()

    assert body["billed_to_owner"] is False, "an inline key was billed to the owner"
    assert body["key_source"] == "user"
    assert body["cap_remaining"] is None, "an allowance was shown that does not apply"
    assert body["provider"] == "anthropic"
    # And the price follows the model that will actually run.
    assert body["dollars_low"] is not None


def test_gate1_the_estimate_and_the_spend_path_resolve_the_same_way(signed_in, ctx,
                                                                   monkeypatch):
    """The structural guarantee behind the fix: one function, not two copies."""
    import inspect

    from web import routes_summaries, routes_usage

    assert hasattr(routes_summaries, "resolve_credentials")
    estimate_src = inspect.getsource(routes_usage.estimate)
    assert "resolve_credentials(" in estimate_src
    # The private second copy must not come back.
    assert not hasattr(routes_usage, "_user_credentials")


def test_gate2_no_credential_anywhere_is_answered_not_a_500(signed_in, ctx,
                                                            monkeypatch):
    """Gate finding 2: the estimate 500-ed where its sibling gives a clean
    answer. It reports the situation instead."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    r = signed_in.post("/api/usage/estimate", json={"papers": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["key_source"] == "missing"
    assert body["billed_to_owner"] is False
    assert body["low"] > 0, "the token counts are still useful without a key"


def test_gate1_the_inline_key_never_travels_in_a_url():
    """It would reach access logs and browser history. A POST body, like the
    sibling routes that take the same field."""
    from web import routes_usage

    routes = [r for r in routes_usage.router.routes
              if getattr(r, "path", "") == "/api/usage/estimate"]
    assert routes, "the estimate route is missing"
    assert set(routes[0].methods) == {"POST"}
