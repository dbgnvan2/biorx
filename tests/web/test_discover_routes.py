"""
Discover Terms: the web route and the shared logic in src/discover.py.

Spec:  docs/web_parity_spec_2026-09-17.md#FP1-B
Fixes from the /csdp review of 2026-09-17 (DT1–DT5 in CHANGELOG):
  DT1 the description was passed where a list was expected and searched as
      single letters;
  DT2 the client polled a payload that never carries results;
  DT3 an owner-key slot was reserved and never released or finalized;
  DT4 an unparsable reply read as "no terms";
  DT5 days_back / max_papers / stop words were constants in code.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import user_store
from src.tokens import UNCOUNTED
from src.discover import (DiscoverParseError, discover_settings, parse_terms,
                          query_to_keywords)

PAPERS = [
    {"title": "Maternal stress and infant cortisol", "abstract": "We measured cortisol."},
    {"title": "Prenatal anxiety cohort", "abstract": "A cohort study."},
]


class FakeOrchestrator:
    """Records the filter it was asked to search and returns fixed papers."""

    def __init__(self, papers=PAPERS, fail_with=None, status=None):
        self.papers, self.fail_with, self.status = papers, fail_with, status
        self.calls = []

    def search(self, filter_dict, source_selection, on_batch, on_progress,
               on_status, should_stop, max_results):
        self.calls.append({"filter_dict": filter_dict, "max_results": max_results})
        if self.fail_with:
            raise self.fail_with
        if self.status:
            on_status(self.status)
        on_batch([SimpleNamespace(to_dict=lambda p=p: dict(p)) for p in self.papers])


def _llm(reply, usage=UNCOUNTED):
    client = MagicMock()
    client.generate.return_value = (reply, usage)
    return client


def _await(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/discover-terms/{job_id}").json()
        if body["status"] in ("done", "error", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"discover job never settled: {body}")


def _run(signed_in, ctx, orch, reply, body=None, usage=UNCOUNTED):
    body = body or {"description": "How does maternal stress affect the infant?",
                    "api_key": "sk-user-inline-key", "provider": "anthropic"}
    with patch.object(ctx, "get_orchestrator", return_value=orch), \
         patch("src.llm_providers.build_client", return_value=_llm(reply, usage)):
        start = signed_in.post("/api/discover-terms", json=body)
        assert start.status_code == 202, start.text
        return _await(signed_in, start.json()["job_id"])


# ── DT1: the description is searched as words, not letters ────────────────────

def test_dt1_route_searches_description_words(signed_in, ctx):
    orch = FakeOrchestrator()
    _run(signed_in, ctx, orch, '{"terms": ["x"]}')
    fd = orch.calls[0]["filter_dict"]
    assert "keywords" not in fd, "a string under 'keywords' is split into letters"
    both = fd["text_groups"][0]["both"]
    assert "maternal" in both and "stress" in both and "infant" in both
    assert "the" not in [t.strip().lower() for t in both.split(",")]

    from src.sources.query_builder import build_europepmc_query
    q = build_europepmc_query(fd)
    assert "maternal" in q
    assert "(m OR a" not in q          # adversarial: the old single-letter query


def test_dt1_query_to_keywords_drops_stop_words_and_duplicates():
    assert query_to_keywords("the stress of Stress in mice", {"the", "of", "in"}) == "stress, mice"
    # Nothing left after filtering: keep the description rather than search nothing.
    assert query_to_keywords("of in", {"of", "in"}) == "of in"


# ── DT2: the client gets the terms ────────────────────────────────────────────

def test_dt2_poll_endpoint_returns_terms(signed_in, ctx):
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["infant cortisol", "prenatal stress"]}')
    assert body["status"] == "done"
    assert body["result"]["terms"] == ["infant cortisol", "prenatal stress"]
    assert body["result"]["papers_found"] == 2


def test_dt2_search_routes_do_not_serve_a_discover_job(signed_in, ctx):
    """The search results route slices a list; a discover result is a dict.
    It must be a 404, not a 500."""
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}')
    job_id = body["job_id"]
    assert signed_in.get(f"/api/searches/{job_id}").status_code == 404
    assert signed_in.get(f"/api/searches/{job_id}/results").status_code == 404


def test_dt2_poll_endpoint_is_per_user(signed_in, other_client, ctx):
    from tests.web.conftest import ACCESS_CODE, account_body
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}')
    other_client.post("/api/session", json=account_body(ACCESS_CODE))
    assert other_client.get(f"/api/discover-terms/{body['job_id']}").status_code == 404


def test_dt2_no_papers_is_reported_as_such(signed_in, ctx):
    body = _run(signed_in, ctx, FakeOrchestrator(papers=[]), '{"terms": ["never asked"]}')
    assert body["status"] == "done"
    assert body["result"]["papers_found"] == 0
    assert body["result"]["terms"] == []


def test_dt2_failed_sources_are_recorded(signed_in, ctx):
    from src.sources.orchestrator import FAILURE_STATUS_MARKER, _SOURCE_LABELS
    orch = FakeOrchestrator(status=f"{_SOURCE_LABELS['pubmed']} {FAILURE_STATUS_MARKER} (error)")
    body = _run(signed_in, ctx, orch, '{"terms": ["a"]}')
    assert body["sources_failed"] == ["pubmed"]


# ── DT3: the owner-key slot is settled ────────────────────────────────────────

@pytest.fixture
def owner_key(ctx, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner-KEY")
    monkeypatch.setenv("SUMMARY_DAILY_CAP_PER_USER", "3")
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()


OWNER_BODY = {"description": "maternal stress"}


def test_gate1_owner_discover_run_records_its_tokens(signed_in, ctx, owner_key):
    """Gate finding 1: this route unpacked the token usage and then dropped it —
    finalize_usage was called without it, so an owner-key discover call was
    settled as tokens_counted = 0 while the numbers sat in memory."""
    from src.tokens import TokenUsage

    spent = TokenUsage(prompt=3000, completion=90, total=3090, counted=True)
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}',
                OWNER_BODY, usage=spent)
    assert body["status"] == "done"

    rows = [dict(r) for r in ctx.db.conn.execute(
        "SELECT * FROM usage_events")]
    assert len(rows) == 1, "the reserved slot is reused, not duplicated"
    assert rows[0]["prompt_tokens"] == 3000
    assert rows[0]["tokens_counted"] == 1
    assert rows[0]["key_source"] == "owner"


def test_gate1_user_key_discover_run_is_recorded_at_all(signed_in, ctx):
    """The worse half of finding 1: a user-key discover run was never written
    to usage_events, because the old block only ran when a slot had been
    reserved — and a user on their own key reserves nothing. Their discover
    spend was invisible forever."""
    from src.tokens import TokenUsage

    spent = TokenUsage(prompt=1500, completion=40, total=1540, counted=True)
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}', usage=spent)
    assert body["status"] == "done"

    rows = [dict(r) for r in ctx.db.conn.execute(
        "SELECT * FROM usage_events")]
    assert len(rows) == 1, "a user-key run must still be logged"
    assert rows[0]["key_source"] != "owner"
    assert rows[0]["prompt_tokens"] == 1500


@pytest.mark.parametrize("error_name", ["bad_shape", "refusal"])
def test_regate1_discover_records_tokens_when_generate_itself_raises(
        signed_in, ctx, owner_key, error_name):
    """Re-gate finding 1: this route calls generate() directly, so it never
    passes through _parsed_or_billed. When generate() raises on an unusable
    reply, `job.token_usage = usage` never runs — yet the model was called and
    billed, and the response reported the cost on the exception.

    Without the recovery this records tokens_counted = 0 for a call that spent
    real money: the same hole the first gate closed for summaries.
    """
    from src.llm_providers import ProviderResponseError
    from src.tokens import TokenUsage

    billed = TokenUsage(prompt=2500, completion=15, total=2515, counted=True)
    blew_up = ProviderResponseError(f"provider {error_name}", usage=billed)
    client = MagicMock()
    client.generate.side_effect = blew_up

    with patch.object(ctx, "get_orchestrator", return_value=FakeOrchestrator()), \
         patch("src.llm_providers.build_client", return_value=client):
        start = signed_in.post("/api/discover-terms", json=OWNER_BODY)
        body = _await(signed_in, start.json()["job_id"])
    assert body["status"] == "error"

    rows = [dict(r) for r in ctx.db.conn.execute("SELECT * FROM usage_events")]
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == 2500, (
        "a billed discover call that raised must not be recorded as costing nothing"
    )
    assert rows[0]["tokens_counted"] == 1


def test_regate1_a_successful_run_keeps_its_own_usage_not_the_exceptions(
        signed_in, ctx, owner_key):
    """The recovery must not overwrite a good reading. A reply that generate()
    returned fine but parse_terms rejected already has its usage; the error
    raised afterwards carries none, and the real numbers must survive."""
    from src.tokens import TokenUsage

    spent = TokenUsage(prompt=1200, completion=30, total=1230, counted=True)
    body = _run(signed_in, ctx, FakeOrchestrator(), "not a list of terms at all",
                OWNER_BODY, usage=spent)
    assert body["status"] == "error"

    rows = [dict(r) for r in ctx.db.conn.execute("SELECT * FROM usage_events")]
    assert rows[0]["prompt_tokens"] == 1200


def test_gate1_a_discover_run_that_never_called_the_model_records_nothing(
        signed_in, ctx, owner_key):
    """The search fails before the model is reached: slot released, no row."""
    _run(signed_in, ctx, FakeOrchestrator(fail_with=RuntimeError("down")),
         '{"terms": ["a"]}', OWNER_BODY)
    n = ctx.db.conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events").fetchone()
    assert n["n"] == 0


def test_dt3_failed_search_gives_the_slot_back(signed_in, ctx, owner_key):
    user_id = signed_in.get("/api/me").json()["user_id"]
    body = _run(signed_in, ctx, FakeOrchestrator(fail_with=RuntimeError("down")),
                '{"terms": ["a"]}', OWNER_BODY)
    assert body["status"] == "error"
    assert user_store.owner_usage_today(ctx.db, user_id) == 0


def test_dt3_successful_run_is_counted_once(signed_in, ctx, owner_key):
    user_id = signed_in.get("/api/me").json()["user_id"]
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}', OWNER_BODY)
    assert body["status"] == "done"
    assert user_store.owner_usage_today(ctx.db, user_id) == 1


# ── DT4: an unusable reply is an error, not "no terms" ────────────────────────

@pytest.mark.parametrize("reply,expected", [
    ('{"terms": ["a b", "c"]}', ["a b", "c"]),
    ('```json\n{"terms": ["fenced"]}\n```', ["fenced"]),
    ('Here you go: {"terms": ["wrapped"]} Hope that helps.', ["wrapped"]),
])
def test_dt4_parse_terms_accepts_common_shapes(reply, expected):
    assert parse_terms(reply) == expected


@pytest.mark.parametrize("reply", [None, "", "I cannot help with that.",
                                   '{"words": ["x"]}', '{"terms": "x"}'])
def test_dt4_parse_terms_rejects_unusable_replies(reply):
    with pytest.raises(DiscoverParseError):
        parse_terms(reply)


def test_dt4_route_reports_unusable_reply_as_error(signed_in, ctx):
    body = _run(signed_in, ctx, FakeOrchestrator(), "Sorry, I can't do that.")
    assert body["status"] == "error"
    assert "did not return a list of terms" in body["error"]


# ── DT5: settings come from config ────────────────────────────────────────────

def test_dt5_settings_come_from_config(signed_in, ctx):
    ctx.llm_config = {**ctx.llm_config,
                      "discover": {"days_back": 12, "max_papers": 7, "stop_words": ["maternal"]}}
    orch = FakeOrchestrator()
    _run(signed_in, ctx, orch, '{"terms": ["a"]}')
    fd = orch.calls[0]["filter_dict"]
    assert fd["days_back"] == 12
    assert orch.calls[0]["max_results"] == 7
    assert "maternal" not in fd["text_groups"][0]["both"].lower()


def test_dt5_repo_config_has_a_discover_block():
    from src.llm_config import load_llm_config
    s = discover_settings(load_llm_config())
    assert s.days_back > 0 and s.max_papers > 0
    assert "the" in s.stop_words


def test_discover_requires_auth(client):
    assert client.post("/api/discover-terms", json={"description": "x"}).status_code == 401
    assert client.get("/api/discover-terms/abc").status_code == 401


def test_dt3_no_papers_gives_the_slot_back(signed_in, ctx, owner_key):
    """Re-sweep finding: the early return (no papers, model never called) kept
    the slot, so empty runs used up the daily cap."""
    user_id = signed_in.get("/api/me").json()["user_id"]
    body = _run(signed_in, ctx, FakeOrchestrator(papers=[]), '{"terms": ["a"]}', OWNER_BODY)
    assert body["status"] == "done"
    assert user_store.owner_usage_today(ctx.db, user_id) == 0


def test_dt3_connection_released_even_if_settling_raises(signed_in, ctx, owner_key, monkeypatch):
    released = []
    real_release = ctx.db.release
    monkeypatch.setattr(ctx.db, "release", lambda: (released.append(1), real_release()))
    monkeypatch.setattr(user_store, "release_usage",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))
    body = _run(signed_in, ctx, FakeOrchestrator(papers=[]), '{"terms": ["a"]}', OWNER_BODY)
    assert body["status"] == "error"
    assert released, "the pooled connection was not released"
