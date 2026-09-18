"""
Tests for the owner-key spend cap.

Spec: docs/implementation_plan_2026-09-15.md#1.4
The access code is shared, so without a ceiling anyone holding it can spend the
owner's credential without limit. A user on their own key is not capped.
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import user_store

PAPER = {"title": "Generative Agents", "abstract": "Interactive simulacra.",
         "doi": "10.1234/agents", "canonical_id": "arxiv:1"}
SUMMARY = {"key_findings": ["f"], "methodology": "m", "conclusions": "c"}


@pytest.fixture
def no_pdf():
    with patch("web.routes_summaries._extract_text", return_value=""):
        yield


@pytest.fixture
def owner_key(ctx, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner-KEY")
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "3")
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()


def _await(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/summaries/{job_id}").json()
        if body["status"] in ("done", "error", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError("never settled")


def _ok_client():
    c = MagicMock()
    c.summarize_paper.return_value = SUMMARY
    return c


def test_owner_key_summaries_are_capped_per_user_per_day(signed_in, owner_key, no_pdf):
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        for i in range(3):
            r = signed_in.post("/api/summaries", json={"paper": PAPER})
            assert r.status_code == 202, f"request {i} was refused early"
            _await(signed_in, r.json()["job_id"])

        refused = signed_in.post("/api/summaries", json={"paper": PAPER})

    assert refused.status_code == 429
    detail = refused.json()["detail"]
    assert "3 summaries" in detail
    assert "your own API key" in detail


def test_the_cap_is_reported_before_it_bites(signed_in, owner_key, no_pdf):
    me = signed_in.get("/api/me").json()
    assert me["owner_summaries_cap"] == 3
    assert me["owner_summaries_remaining"] == 3

    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        r = signed_in.post("/api/summaries", json={"paper": PAPER})
        _await(signed_in, r.json()["job_id"])

    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 2


def test_user_own_key_is_not_capped(signed_in, owner_key, enc_secret, no_pdf):
    signed_in.put("/api/me/llm-key",
                  json={"provider": "deepseek", "api_key": "sk-user-OWNKEY"})

    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        for _ in range(5):          # well past the cap of 3
            r = signed_in.post("/api/summaries", json={"paper": PAPER})
            assert r.status_code == 202
            assert r.json()["key_source"] == "user"
            _await(signed_in, r.json()["job_id"])


def test_the_cap_counts_only_owner_key_usage(ctx, signed_in, owner_key, no_pdf):
    user_id = signed_in.get("/api/me").json()["user_id"]
    for _ in range(10):
        user_store.record_usage(ctx.db, user_id, "summary", "deepseek",
                                "deepseek-chat", "user")
    assert user_store.owner_usage_today(ctx.db, user_id) == 0

    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        assert signed_in.post("/api/summaries",
                              json={"paper": PAPER}).status_code == 202


def test_one_users_spending_does_not_cap_another(ctx, app, owner_key, no_pdf):
    from fastapi.testclient import TestClient
    from tests.web.conftest import ACCESS_CODE, account_body

    alice = TestClient(app)
    bob = TestClient(app)

    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        alice.post("/api/session", json=account_body(ACCESS_CODE))
        for _ in range(3):
            r = alice.post("/api/summaries", json={"paper": PAPER})
            _await(alice, r.json()["job_id"])
        assert alice.post("/api/summaries",
                          json={"paper": PAPER}).status_code == 429

        bob.post("/api/session", json=account_body(ACCESS_CODE))
        assert bob.post("/api/summaries", json={"paper": PAPER}).status_code == 202


def test_usage_rows_never_contain_a_key(ctx, signed_in, owner_key, no_pdf):
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        r = signed_in.post("/api/summaries", json={"paper": PAPER})
        _await(signed_in, r.json()["job_id"])

    rows = ctx.db.conn.execute("SELECT * FROM usage_events").fetchall()
    assert rows
    for row in rows:
        blob = " ".join(str(v) for v in dict(row).values())
        assert "sk-" not in blob


# ── The cap must hold under a burst, not only in sequence ─────────────────────

def test_the_cap_holds_against_simultaneous_requests(app, owner_key, no_pdf):
    """
    Regression for a proven check-then-act race: the cap was read at submission
    but usage was written only when the job finished, so requests arriving
    together all observed the same count and all passed. Measured: cap of 3,
    six rapid requests, six acceptances.
    """
    import threading

    from fastapi.testclient import TestClient
    from tests.web.conftest import ACCESS_CODE, account_body

    client = TestClient(app)
    client.post("/api/session", json=account_body(ACCESS_CODE))

    codes = []
    codes_lock = threading.Lock()
    start = threading.Event()

    def fire():
        start.wait(timeout=5)
        r = client.post("/api/summaries", json={"paper": PAPER})
        with codes_lock:
            codes.append(r.status_code)

    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(timeout=10)

    accepted = codes.count(202)
    refused = codes.count(429)
    assert accepted == 3, f"cap of 3 admitted {accepted} (codes: {sorted(codes)})"
    assert refused == 5


def test_a_slot_is_returned_when_the_job_fails_before_the_provider(
    ctx, signed_in, owner_key
):
    """
    A failure that never reached the provider cost nothing, so it must not
    consume the user's allowance.
    """
    with patch("web.routes_summaries._extract_text", return_value=""):
        with patch("src.llm_providers.build_client", return_value=_ok_client()):
            r = signed_in.post("/api/summaries",
                               json={"paper": {"title": "t", "abstract": ""}})
            _await(signed_in, r.json()["job_id"])

    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 3


def test_a_slot_is_kept_when_the_provider_itself_failed(ctx, signed_in, owner_key,
                                                        no_pdf):
    """A call that reached the provider may have cost money; the cap is a spend
    ceiling, so that attempt still counts."""
    failing = MagicMock()
    failing.summarize_paper.side_effect = RuntimeError("provider exploded")
    with patch("src.llm_providers.build_client", return_value=failing):
        r = signed_in.post("/api/summaries", json={"paper": PAPER})
        _await(signed_in, r.json()["job_id"])

    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 2
