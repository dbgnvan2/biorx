"""
Tests for the summary routes, including D2's three credential modes and the
owner-key spend cap.

Spec: docs/implementation_plan_2026-09-15.md#2.2, #1.4, D2
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import user_store
from tests.web.conftest import ACCESS_CODE

PAPER = {
    "title": "Generative Agents", "abstract": "A study of interactive simulacra.",
    "doi": "10.1234/agents", "authors": "Park J", "date": "2026-09-01",
    "canonical_id": "arxiv:2609.00001",
}

SUMMARY = {
    "key_findings": ["Agents cooperate"],
    "methodology": "Simulation.",
    "conclusions": "Scale matters.",
}


@pytest.fixture
def no_pdf():
    """Summaries run from the abstract; PDF fetching is its own concern."""
    with patch("web.routes_summaries._extract_text", return_value=""):
        yield


def _await(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/summaries/{job_id}").json()
        if body["status"] in ("done", "error", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"summary job never settled: {body}")


def _client_returning(summary):
    client = MagicMock()
    client.summarize_paper.return_value = summary
    return client


# ── D2, mode 1: owner key only ────────────────────────────────────────────────

def test_summary_with_owner_key_only(ctx, signed_in, monkeypatch, no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner-OWNERKEY")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        start = signed_in.post("/api/summaries", json={"paper": PAPER})
        assert start.status_code == 202
        assert start.json()["key_source"] == "owner"
        body = _await(signed_in, start.json()["job_id"])

    assert body["status"] == "done"
    assert body["result"]["key_findings"] == SUMMARY["key_findings"]
    assert body["result"]["model"] == "claude-sonnet-5"


# ── D2, mode 2: the user's own key overrides the owner's ──────────────────────

def test_user_key_overrides_owner_key(ctx, signed_in, monkeypatch, enc_secret, no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner-OWNERKEY")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    signed_in.put("/api/me/llm-key",
                  json={"provider": "deepseek", "api_key": "sk-user-USERKEY1234"})

    seen = {}

    def capture(pconf, api_key, max_chars):
        seen["provider"] = pconf.name
        seen["key"] = api_key
        return _client_returning(SUMMARY)

    with patch("src.llm_providers.build_client", side_effect=capture):
        start = signed_in.post("/api/summaries", json={"paper": PAPER})
        assert start.json()["key_source"] == "user"
        body = _await(signed_in, start.json()["job_id"])

    assert body["status"] == "done"
    assert seen["provider"] == "deepseek"
    assert seen["key"] == "sk-user-USERKEY1234"


# ── D2, mode 3: no key at all — a clean error, not a crash ────────────────────

def test_no_key_returns_clean_error_and_does_not_crash(ctx, signed_in, monkeypatch,
                                                       no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    r = signed_in.post("/api/summaries", json={"paper": PAPER})

    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "ANTHROPIC_API_KEY" in detail        # what the operator must set
    assert "LLM settings" in detail             # what the user can do
    assert signed_in.get("/api/me").status_code == 200   # app still healthy


# ── Failures inside the job ───────────────────────────────────────────────────

def test_a_provider_failure_lands_as_an_error_status_not_a_hang(ctx, signed_in,
                                                                monkeypatch, no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    failing = MagicMock()
    failing.summarize_paper.side_effect = RuntimeError("provider exploded")
    with patch("src.llm_providers.build_client", return_value=failing):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "error"
    assert "provider exploded" in body["error"]


def test_an_ollama_none_return_is_an_error_not_a_blank_summary(ctx, signed_in,
                                                               monkeypatch, no_pdf):
    """
    OllamaClient returns None on failure while the hosted clients raise. The
    route must not store None as a summary (chunk-3 gate finding 3, P22).
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    with patch("src.llm_providers.build_client", return_value=_client_returning(None)):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "error"
    assert "no summary" in body["error"].lower()


def test_a_paper_with_no_text_at_all_is_an_error(ctx, signed_in, monkeypatch, no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post(
            "/api/summaries", json={"paper": {**PAPER, "abstract": ""}}
        ).json()["job_id"]
        body = _await(signed_in, job_id)
    assert body["status"] == "error"


def test_an_empty_paper_is_rejected(signed_in):
    assert signed_in.post("/api/summaries", json={"paper": {}}).status_code == 400


# ── Persistence and provenance ────────────────────────────────────────────────

def test_a_summary_is_stored_with_its_model_and_author(ctx, signed_in, monkeypatch,
                                                       no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    user_id = signed_in.get("/api/me").json()["user_id"]

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)

    row = ctx.db.conn.execute(
        "SELECT model_version, created_by_user_id FROM summaries"
    ).fetchone()
    assert row["model_version"] == "claude-sonnet-5"
    assert row["created_by_user_id"] == user_id
    assert signed_in.get(f"/api/papers/{body['result']['paper_id']}/summary"
                         ).status_code == 200


def test_one_user_cannot_read_anothers_summary_job(ctx, app, monkeypatch, no_pdf):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    alice = TestClient(app)
    bob = TestClient(app)

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        alice.post("/api/session", json={"access_code": ACCESS_CODE})
        job_id = alice.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        _await(alice, job_id)

        bob.post("/api/session", json={"access_code": ACCESS_CODE})
        assert bob.get(f"/api/summaries/{job_id}").status_code == 404
