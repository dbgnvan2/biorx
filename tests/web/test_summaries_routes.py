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
from src.tokens import UNCOUNTED
from tests.web.conftest import ACCESS_CODE, account_body

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



def _full_text(ctx, paper, outcome=None, by_title=None):
    """Stand-in for a found PDF: summaries now need full text, or the abstract
    is kept without calling the model (plan 2026-09-19 C1)."""
    if outcome is not None:
        outcome.update(full_text="used", text_source="Unpaywall")
    return "Full text of the paper: methods, results and discussion."

@pytest.fixture
def no_pdf():
    """No full text anywhere: the abstract stands in and the model is not called
    (plan 2026-09-19 C1). Finding text is its own concern (test_fulltext.py)."""
    with patch("web.routes_summaries._extract_text", return_value=""):
        yield


@pytest.fixture
def with_full_text():
    """Full text was found, so the model runs."""
    with patch("web.routes_summaries._extract_text", side_effect=_full_text):
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
    client.summarize_paper.return_value = (summary, UNCOUNTED)
    return client


# ── D2, mode 1: owner key only ────────────────────────────────────────────────

def test_summary_with_owner_key_only(ctx, signed_in, monkeypatch, with_full_text):
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

def test_user_key_overrides_owner_key(ctx, signed_in, monkeypatch, enc_secret, with_full_text):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner-OWNERKEY")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    signed_in.put("/api/me/llm-key",
                  json={"provider": "deepseek", "api_key": "sk-user-USERKEY1234"})

    seen = {}

    def capture(pconf, api_key, max_chars, model_override=""):
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
                                                       with_full_text):
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
                                                                monkeypatch, with_full_text):
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


def test_m1b3_a_failed_run_still_records_the_tokens_it_spent(ctx, signed_in,
                                                            monkeypatch, with_full_text):
    """M1.B.3: the model ran and was billed, then something downstream failed.
    The summary is lost; the record of what it cost must not be, or a run that
    spent real money is invisible in the meter.

    The provider is made to return a good usage alongside a reply that cannot
    be coerced into a summary — billed, then unusable.
    """
    from src import user_store
    from src.tokens import TokenUsage

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    spent = TokenUsage(prompt=6000, completion=200, total=6200, counted=True)
    client = MagicMock()
    # A reply the model was billed for, which then blows up on use.
    client.summarize_paper.return_value = (None, spent)
    with patch("src.llm_providers.build_client", return_value=client):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "error"
    rows = [dict(r) for r in ctx.db.conn.execute(
        "SELECT * FROM usage_events WHERE kind = 'summary'")]
    assert len(rows) == 1, "the reserved slot is reused, not duplicated"
    assert rows[0]["prompt_tokens"] == 6000
    assert rows[0]["tokens_counted"] == 1


def test_m1b2_a_run_that_never_reached_the_provider_records_no_tokens(
        ctx, signed_in, monkeypatch, with_full_text):
    """The mirror case: nothing was spent, so the reserved slot goes back and
    no token row survives. A cap is a spend ceiling, not an attempt counter."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-owner")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    client = MagicMock()
    # Fails while gathering the text, before the model is ever called.
    with patch("src.llm_providers.build_client", return_value=client), \
         patch("web.routes_summaries._extract_text",
               side_effect=RuntimeError("could not read the paper")):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        _await(signed_in, job_id)
    client.summarize_paper.assert_not_called()

    rows = ctx.db.conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events WHERE kind = 'summary'").fetchone()
    assert rows["n"] == 0


def test_an_ollama_none_return_is_an_error_not_a_blank_summary(ctx, signed_in,
                                                               monkeypatch, with_full_text):
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
                                                       with_full_text):
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


def test_one_user_cannot_read_anothers_summary_job(ctx, app, monkeypatch, with_full_text):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    alice = TestClient(app)
    bob = TestClient(app)

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        alice.post("/api/session", json=account_body(ACCESS_CODE))
        job_id = alice.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        _await(alice, job_id)

        bob.post("/api/session", json=account_body(ACCESS_CODE))
        assert bob.get(f"/api/summaries/{job_id}").status_code == 404


def test_an_oversized_paper_is_refused_at_the_boundary(signed_in, monkeypatch, with_full_text):
    """
    The paper arrives in the request body, so its size is user-controlled.
    Refuse it at the entry point rather than truncating it silently inside the
    prompt builder (security S2).
    """
    from web.routes_summaries import MAX_PAPER_BYTES

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx_module = __import__("src.llm_config", fromlist=["x"])
    huge = {"title": "t", "abstract": "a" * (MAX_PAPER_BYTES + 1000)}

    r = signed_in.post("/api/summaries", json={"paper": huge})

    assert r.status_code == 413
    assert "larger than" in r.json()["detail"]


def test_a_normal_sized_paper_is_accepted(signed_in, ctx, monkeypatch, with_full_text):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        r = signed_in.post("/api/summaries", json={"paper": PAPER})
    assert r.status_code == 202


# ── Cold-review findings ──────────────────────────────────────────────────────

def test_a_summary_is_saved_even_when_the_paper_is_already_in_the_database(
    ctx, signed_in, monkeypatch, with_full_text
):
    """
    insert_paper() returns None for a DOI already stored — the normal case when
    a colleague has saved the paper already. Treating that as failure meant the
    summary was computed, billed and displayed, then silently not saved.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    # The paper is already there, as if someone had run a search that saved it.
    first_id = ctx.db.insert_paper(PAPER)
    assert first_id is not None
    assert ctx.db.insert_paper(PAPER) is None, "the premise of this test changed"

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "done"
    stored = ctx.db.get_summary(first_id)
    assert stored is not None, "the summary was billed but never saved"
    assert stored["conclusions"] == SUMMARY["conclusions"]


def test_an_ollama_summary_with_empty_fields_is_an_error_not_a_blank_card(
    ctx, signed_in, monkeypatch, with_full_text
):
    """
    OllamaClient does no validation: its text parser returns a dict of empty
    fields when the model answers in a shape it does not recognise. The hosted
    clients reject that; every provider must.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    blank = {"key_findings": [], "methodology": "", "conclusions": ""}
    with patch("src.llm_providers.build_client", return_value=_client_returning(blank)):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "error"
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 0


def test_an_existing_summary_is_returned_without_running_the_model(
    ctx, signed_in, monkeypatch, with_full_text
):
    """
    Summaries are shared per paper. Re-running one costs a model call for an
    answer already stored — and, on the owner key, a slot from the daily cap.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        _await(signed_in, job_id)

    client = MagicMock()
    with patch("src.llm_providers.build_client", return_value=client):
        found = signed_in.post("/api/summaries/lookup", json={"paper": PAPER})

    assert found.status_code == 200
    assert found.json()["conclusions"] == SUMMARY["conclusions"]
    client.summarize_paper.assert_not_called()


def test_lookup_is_404_for_a_paper_nobody_has_summarized(signed_in):
    r = signed_in.post("/api/summaries/lookup",
                       json={"paper": {"doi": "10.9999/never-seen"}})
    assert r.status_code == 404


def test_lookup_is_404_for_a_paper_with_no_doi(signed_in):
    assert signed_in.post("/api/summaries/lookup",
                          json={"paper": {"title": "no doi"}}).status_code == 404


# ── N1: papers without a DOI ──────────────────────────────────────────────────

def _arxiv_paper():
    """A real arXiv record, built by the adapter — its DOI is empty."""
    from src.sources.arxiv import ArxivAdapter
    return ArxivAdapter().normalize({
        "arxiv_id_full": "2609.04321v1", "title": "Silicon samples revisited",
        "abstract": "We revisit algorithmic fidelity in LLM survey responses.",
        "authors": ["A. Argyle"], "published": "2026-09-12", "categories": ["cs.CL"],
    }).to_dict()


def test_n1_arxiv_summary_is_saved_and_looked_up(ctx, signed_in, monkeypatch, with_full_text):
    """
    Found by the 2026-09-16 smoke test: a DOI-less paper could never be stored,
    so an arXiv summary was billed and lost, and the lookup — keyed on DOI —
    could never find it, so every click paid again.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    paper = _arxiv_paper()
    assert paper["doi"] == ""

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": paper}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "done"
    assert body["result"]["paper_id"] is not None, "the summary was not saved"

    client = MagicMock()
    with patch("src.llm_providers.build_client", return_value=client):
        found = signed_in.post("/api/summaries/lookup", json={"paper": paper})
    assert found.status_code == 200
    assert found.json()["conclusions"] == SUMMARY["conclusions"]
    client.summarize_paper.assert_not_called()


def test_n1_resummarizing_a_stored_arxiv_paper_saves_to_the_same_row(
    ctx, signed_in, monkeypatch, with_full_text
):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    paper = _arxiv_paper()
    existing_id = ctx.db.insert_paper(paper)

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": paper}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["result"]["paper_id"] == existing_id
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


# ── N2: recover a missing abstract before summarizing ─────────────────────────

FLAMING = {
    "title": "Interparental conflict and adolescent online flaming",
    "abstract": "", "doi": "", "pmcid": "PMC13572605",
    "best_oa_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC13572605/",
    "canonical_id": "pmcid:PMC13572605",
}


def test_n2_missing_abstract_is_recovered_before_summarizing(ctx, signed_in, monkeypatch, no_pdf):
    """The smoke-test paper: open access on PMC, no abstract in the record."""
    from src.paper_meta import AbstractRecovery

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    client = _client_returning(SUMMARY)

    with patch("web.routes_summaries.recover_abstract",
               return_value=AbstractRecovery("Recovered abstract text.", "pmc_fulltext",
                                             ["pmc_fulltext"])), \
         patch("src.llm_providers.build_client", return_value=client):
        job_id = signed_in.post("/api/summaries", json={"paper": FLAMING}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "done", body
    # No full text: the recovered abstract stands in and the model is not
    # called (plan 2026-09-19 C1 — this test predates it).
    client.summarize_paper.assert_not_called()
    assert body["result"]["source_text"] == "abstract"
    assert body["result"]["abstract"] == "Recovered abstract text."
    stored = ctx.db.find_paper(FLAMING)
    assert stored["abstract"] == "Recovered abstract text.", \
        "the recovered abstract should be stored with the paper, not fetched again"


def test_n2_recovery_is_not_attempted_when_the_record_has_an_abstract(ctx, signed_in, monkeypatch, with_full_text):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    with patch("web.routes_summaries.recover_abstract") as rec, \
         patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        _await(signed_in, job_id)
    rec.assert_not_called()


def test_n2_correction_notice_gets_a_specific_message(ctx, signed_in, monkeypatch, no_pdf):
    """
    A correction notice has no abstract anywhere; refusing it is right, but
    "no downloadable text" sends the user looking for a problem that isn't there.
    """
    from src.paper_meta import AbstractRecovery

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    notice = {"title": "Correction to: Exploring the association between stress and skin",
              "abstract": "", "doi": "10.1007/s44192-026-00588-0",
              "canonical_id": "doi:10.1007/s44192-026-00588-0"}
    client = _client_returning(SUMMARY)
    with patch("web.routes_summaries.recover_abstract",
               return_value=AbstractRecovery("", None, ["europepmc"])), \
         patch("src.llm_providers.build_client", return_value=client):
        job_id = signed_in.post("/api/summaries", json={"paper": notice}).json()["job_id"]
        body = _await(signed_in, job_id)

    assert body["status"] == "error"
    assert "correction" in body["error"].lower()
    client.summarize_paper.assert_not_called()
    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] >= 0


def test_n2_an_unrecoverable_article_still_says_what_was_tried(ctx, signed_in, monkeypatch, no_pdf):
    from src.paper_meta import AbstractRecovery

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    with patch("web.routes_summaries.recover_abstract",
               return_value=AbstractRecovery("", None, ["europepmc", "crossref", "openalex"])), \
         patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries",
                                json={"paper": {**FLAMING, "title": "An ordinary article"}}).json()["job_id"]
        body = _await(signed_in, job_id)
    assert body["status"] == "error"
    assert "europepmc" in body["error"] and "openalex" in body["error"]


def test_r5_unreachable_source_is_named_apart_from_empty_ones(ctx, signed_in, monkeypatch, no_pdf):
    """Review finding 5: an outage is 'could not reach', not 'looked in'."""
    from src.paper_meta import AbstractRecovery

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    with patch("web.routes_summaries.recover_abstract",
               return_value=AbstractRecovery("", None, ["europepmc", "crossref"], ["crossref"])), \
         patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries",
                                json={"paper": {**FLAMING, "title": "An ordinary article"}}).json()["job_id"]
        body = _await(signed_in, job_id)
    assert "(looked in: europepmc)" in body["error"]
    assert "Could not reach: crossref" in body["error"]


def test_rl2_summary_made_from_a_saved_list_shows_on_that_list(ctx, signed_in, monkeypatch, with_full_text):
    """RL2 end to end: the paper dict the References tab holds (from GET
    /items) is summarized through the real route, and the list's summaries
    then include it — the ✓ badge's source of truth."""
    from src import user_store
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    paper = {"title": "Agents in a saved list", "abstract": "We simulate agents at scale.",
             "doi": "10.9/rl2", "canonical_id": "doi:10.9/rl2", "source": "arxiv"}
    list_id = signed_in.post("/api/references", json={"name": "Saved"}).json()["id"]
    user_store.add_reference_item(ctx.db, list_id, ctx.db.insert_paper(paper))
    held = signed_in.get(f"/api/references/{list_id}/items").json()["items"][0]["paper"]
    assert signed_in.get(f"/api/references/{list_id}/summaries").json()["summaries"] == []

    with patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries", json={"paper": held}).json()["job_id"]
        assert _await(signed_in, job_id)["status"] == "done"

    listed = signed_in.get(f"/api/references/{list_id}/summaries").json()["summaries"]
    assert [s["title"] for s in listed] == ["Agents in a saved list"]
    assert listed[0]["key_findings"] == ["Agents cooperate"]



# ── FT1: no model call without full text (plan 2026-09-19) ───────────────────

def test_ft1_1_no_full_text_stores_the_abstract_without_a_model_call(ctx, signed_in, monkeypatch, no_pdf):
    """No full text: the model is not called, no owner usage is spent, and the
    abstract is stored as the entry, marked source_text=abstract."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "owner-key-for-test")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    client = _client_returning(SUMMARY)
    before = signed_in.get("/api/me").json()["owner_summaries_remaining"]
    with patch("src.llm_providers.build_client", return_value=client):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)
    assert body["status"] == "done", body
    client.summarize_paper.assert_not_called()
    assert body["result"]["source_text"] == "abstract"
    assert body["result"]["abstract"] == PAPER["abstract"]
    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == before
    stored = ctx.db.get_summary(ctx.db.find_paper(PAPER)["id"])
    assert (stored["source_text"], stored["summary_text"], stored["model_version"]) == \
        ("abstract", PAPER["abstract"], "")


def test_ft1_3_full_text_source_is_recorded(ctx, signed_in, monkeypatch, with_full_text):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    client = _client_returning(SUMMARY)
    with patch("src.llm_providers.build_client", return_value=client):
        job_id = signed_in.post("/api/summaries", json={"paper": PAPER}).json()["job_id"]
        body = _await(signed_in, job_id)
    assert client.summarize_paper.call_args.args[1].startswith("Full text of the paper")
    assert (body["result"]["source_text"], body["result"]["text_source"]) == ("full_text", "Unpaywall")
    stored = ctx.db.get_summary(ctx.db.find_paper(PAPER)["id"])
    assert (stored["source_text"], stored["text_source"]) == ("full_text", "Unpaywall")


def test_ft1_find_by_title_reaches_the_finder(ctx, signed_in, monkeypatch):
    """The per-request title-search switch from Settings arrives at _extract_text."""
    seen = {}

    def fake(ctx_, paper, outcome=None, by_title=None):
        seen["by_title"] = by_title
        return ""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ctx.llm_config = __import__("src.llm_config", fromlist=["x"]).load_llm_config()
    with patch("web.routes_summaries._extract_text", side_effect=fake), \
         patch("src.llm_providers.build_client", return_value=_client_returning(SUMMARY)):
        job_id = signed_in.post("/api/summaries",
                                json={"paper": PAPER, "find_by_title": False}).json()["job_id"]
        _await(signed_in, job_id)
    assert seen["by_title"] is False
