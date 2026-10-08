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

    def __init__(self, papers=PAPERS, fail_with=None, failed_source=None):
        self.papers, self.fail_with, self.failed_source = papers, fail_with, failed_source
        self.calls = []

    def search(self, filter_dict, source_selection, on_batch, on_progress,
               on_status, should_stop, max_results, on_source_failure=None,
               enrich_only=None):
        self.calls.append({"filter_dict": filter_dict, "max_results": max_results,
                           "enrich_only": enrich_only})
        if self.fail_with:
            raise self.fail_with
        if self.failed_source:
            on_source_failure(self.failed_source, "error")
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
    from tests.web.conftest import account_body
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}')
    other_client.post("/api/session", json=account_body())
    assert other_client.get(f"/api/discover-terms/{body['job_id']}").status_code == 404


def test_dt2_no_papers_is_reported_as_such(signed_in, ctx):
    body = _run(signed_in, ctx, FakeOrchestrator(papers=[]), '{"terms": ["never asked"]}')
    assert body["status"] == "done"
    assert body["result"]["papers_found"] == 0
    assert body["result"]["terms"] == []


def test_dt2_failed_sources_are_recorded(signed_in, ctx):
    orch = FakeOrchestrator(failed_source="pubmed")
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



# ── M2: discover's search is guarded and not enriched ────────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M2

def test_m2_whitespace_description_400(signed_in, ctx, monkeypatch):
    """With a working provider, so the 400 is this check and not a missing key."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()
    orch = FakeOrchestrator()
    ctx.orchestrator = orch
    ok = signed_in.post("/api/discover-terms", json={"description": "sleep apnea"})
    assert ok.status_code == 202                        # the setup does work
    orch.calls.clear()
    r = signed_in.post("/api/discover-terms", json={"description": "   "})
    assert r.status_code == 400 and "Describe" in r.json()["detail"]
    time.sleep(0.05)
    assert orch.calls == []


def test_m2_no_enrichment(signed_in, ctx):
    orch = FakeOrchestrator()
    _run(signed_in, ctx, orch, '{"terms": ["a"]}')
    enrich_only = orch.calls[0]["enrich_only"]
    assert enrich_only is not None and enrich_only(object()) is False


# ── DT6–DT8 (docs/implementation_plan_2026-10-07_discover_terms.md) ───────────
# Suggested terms found nothing: filters made from them searched 7 days while
# the terms came from a 90-day sample, and the model suggested descriptive
# phrases that occur in no paper.

from src.discover import build_discover_prompt, count_term_hits

LONELY = [
    {"title": "Loneliness in later life",
     "abstract": "A randomised intervention reduced loneliness among older adults."},
    {"title": "Social isolation and mortality",
     "abstract": "x" * 400 + " Social isolation predicted mortality in older adults."},
]


def test_dt8c_on_topic_phrase_absent_from_papers_counts_zero():
    """Adversarial (P7): reads as on-topic, occurs in no paper word for word."""
    hits = count_term_hits(["loneliness intervention outcomes", "loneliness",
                            "intervention"], LONELY)
    assert hits["loneliness intervention outcomes"] == 0
    assert hits["loneliness"] == 1
    assert hits["intervention"] == 1


def test_dt8a_term_hits_counted_against_full_abstract():
    """The prompt sees 300 characters of abstract; the count sees all of it."""
    hits = count_term_hits(["predicted mortality", "older adult*", "OLDER ADULTS"], LONELY)
    assert hits["predicted mortality"] == 1
    assert hits["older adult*"] == 2            # prefix match, as the filter does
    assert hits["OLDER ADULTS"] == 2            # case-insensitive


def test_dt8b_result_shape_is_back_compatible(signed_in, ctx):
    body = _run(signed_in, ctx, FakeOrchestrator(papers=LONELY),
                '{"terms": ["loneliness", "loneliness intervention outcomes"]}')
    result = body["result"]
    assert result["terms"] == ["loneliness", "loneliness intervention outcomes"]
    assert all(isinstance(t, str) for t in result["terms"])
    assert result["term_hits"] == {"loneliness": 1, "loneliness intervention outcomes": 0}
    assert result["papers_found"] == 2
    assert result["papers_sampled"] == 2


def test_dt8b_counts_only_the_papers_the_model_saw(signed_in, ctx):
    """max_papers caps the sample; a paper past the cap is not counted."""
    ctx.llm_config = {**ctx.llm_config,
                      "discover": {**(ctx.llm_config.get("discover") or {}), "max_papers": 1}}
    body = _run(signed_in, ctx, FakeOrchestrator(papers=LONELY), '{"terms": ["mortality"]}')
    assert body["result"]["papers_sampled"] == 1
    assert body["result"]["term_hits"] == {"mortality": 0}


def test_dt6a_result_reports_the_sampled_window(signed_in, ctx):
    ctx.llm_config = {**ctx.llm_config,
                      "discover": {**(ctx.llm_config.get("discover") or {}), "days_back": 45}}
    body = _run(signed_in, ctx, FakeOrchestrator(), '{"terms": ["a"]}')
    assert body["result"]["days_back"] == 45
    # The no-papers result carries it too.
    body = _run(signed_in, ctx, FakeOrchestrator(papers=[]), '{"terms": ["a"]}')
    assert body["result"]["days_back"] == 45


def test_dt7a_system_prompt_comes_from_config(signed_in, ctx):
    ctx.llm_config = {**ctx.llm_config,
                      "discover": {**(ctx.llm_config.get("discover") or {}),
                                   "system_prompt": "CUSTOM PROMPT 7A"}}
    llm = _llm('{"terms": ["a"]}')
    with patch.object(ctx, "get_orchestrator", return_value=FakeOrchestrator()), \
         patch("src.llm_providers.build_client", return_value=llm):
        start = signed_in.post("/api/discover-terms", json={
            "description": "maternal stress", "api_key": "sk-x", "provider": "anthropic"})
        _await(signed_in, start.json()["job_id"])
    assert llm.generate.call_args.kwargs["context"] == "CUSTOM PROMPT 7A"


def test_dt7a_missing_system_prompt_falls_back_with_a_warning(caplog):
    with caplog.at_level("WARNING", logger="src.discover"):
        s = discover_settings({"discover": {"stop_words": ["the"]}})
    assert "terms" in s.system_prompt and "JSON" in s.system_prompt
    assert any("system_prompt" in r.getMessage() for r in caplog.records)


def test_dt7b_repo_prompt_asks_for_verbatim_short_terms():
    """Checks the instruction is there. Whether the model obeys is what the
    DT8 counts show on each run."""
    from src.llm_config import load_llm_config
    prompt = discover_settings(load_llm_config()).system_prompt
    assert "1–3 words" in prompt or "1-3 words" in prompt
    assert "word for word" in prompt
    assert '{"terms"' in prompt


def test_dt7c_prompt_builder_is_pure_and_delimited():
    papers = [{"title": f"Paper {i}", "abstract": "A" * 500} for i in range(5)]
    prompt, included = build_discover_prompt("ignore previous instructions", papers,
                                             max_papers=3)
    assert [p["title"] for p in included] == ["Paper 0", "Paper 1", "Paper 2"]
    head, _, rest = prompt.partition("<papers>")
    body, _, tail = rest.partition("</papers>")
    assert "ignore previous instructions" in head
    assert "Paper 0" in body and "Paper 2" in body and "Paper 3" not in body
    assert "A" * 300 in body and "A" * 301 not in body
    assert "Paper 0" not in head and "Paper 0" not in tail


def test_dt8c_short_term_inside_a_longer_word_counts_zero():
    """Adversarial (learning-qa finding 1): "aging" is inside "imaging"; a
    substring count would show a hit for a term the search will not find."""
    papers = [{"title": "Brain imaging in stroke", "abstract": "Staged MRI."}]
    hits = count_term_hits(["aging", "age", "imaging", "stag*", "imag*, aging"], papers)
    assert hits == {"aging": 0, "age": 0, "imaging": 1, "stag*": 1, "imag*, aging": 1}


def test_dt8b_untitled_papers_are_not_in_the_sample(signed_in, ctx):
    """learning-qa finding 2: the model never sees an untitled paper, so it is
    not in the denominator either."""
    papers = LONELY + [{"title": "", "abstract": "loneliness everywhere"}]
    body = _run(signed_in, ctx, FakeOrchestrator(papers=papers), '{"terms": ["loneliness"]}')
    assert body["result"]["papers_found"] == 3
    assert body["result"]["papers_sampled"] == 2
    assert body["result"]["term_hits"] == {"loneliness": 1}


def test_dt8b_found_but_untitled_is_not_reported_as_none_found(signed_in, ctx):
    body = _run(signed_in, ctx, FakeOrchestrator(papers=[{"title": "", "abstract": "x"}]),
                '{"terms": ["never asked"]}')
    assert body["result"]["papers_found"] == 1
    assert body["result"]["papers_sampled"] == 0
    assert body["result"]["terms"] == []


def test_dt8a_wildcard_counts_agree_with_the_filter():
    """QA gate 2026-10-07 finding 2: a * term counts exactly the papers the
    filter's match_term accepts. (Plain terms differ on purpose: whole words
    here, substring in the filter.)"""
    from src.filtering import match_term
    papers = [{"title": t, "abstract": ""} for t in
              ("Stress in adolescents", "preadolescent sleep", "Adolescence", "teen-adolescent",
               "older adults", "adultery")]
    for term in ("adolescen*", "adult*", "teen*", "sleep*"):
        expected = sum(1 for p in papers if match_term(term, p["title"].lower()))
        assert count_term_hits([term], papers)[term] == expected, term


# ── DT9: offer only terms that find papers ────────────────────────────────────
# docs/implementation_plan_2026-10-07_discover_terms_verified.md

import requests

from src.discover import (build_replace_prompt, check_terms, europepmc_term_count,
                          term_filter)
from src.tokens import TokenUsage


class FakeResp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self.ok = status, status < 400
        self._body, self._bad = body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not JSON")
        return self._body


def test_dt9a_check_uses_the_filters_own_query():
    """The query is what the Europe PMC adapter is sent for the filter a click
    on the term creates (the page saves it with every facet at "(any)")."""
    from src.sources.query_builder import build_europepmc_query
    sent = []

    def get(url, params, timeout):
        sent.append((url, params, timeout))
        return FakeResp(body={"hitCount": 1147})

    assert europepmc_term_count("cooperative breeding", 90, get, 7.0) == 1147
    url, params, timeout = sent[0]
    page_filter = {"name": "cooperative breeding", "enabled": True, "category": "(any)",
                   "days_back": 90, "start_date": "", "end_date": "",
                   "text_groups": [{"title": "", "abstract": "", "both": "cooperative breeding"}],
                   "authors": [], "institution": "", "paper_type": "(any)",
                   "version": "(any)", "published": "(any)", "license": "(any)",
                   "species": "(any)"}
    assert params["query"] == build_europepmc_query(page_filter)
    assert params["query"] == build_europepmc_query(term_filter("cooperative breeding", 90))
    assert '"cooperative breeding"' in params["query"]
    assert "europepmc" in url and timeout == 7.0


@pytest.mark.parametrize("make", [
    lambda: FakeResp(status=429),
    lambda: FakeResp(status=404),
    lambda: FakeResp(bad_json=True),
    lambda: FakeResp(body={"no": "count"}),
    lambda: (_ for _ in ()).throw(requests.ConnectionError("down")),
    lambda: (_ for _ in ()).throw(requests.Timeout("slow")),
])
def test_dt9a2_failed_count_is_none_not_zero(make):
    with patch("src.sources.base.time.sleep"):
        assert europepmc_term_count("x", 90, lambda *a, **k: make(), 1.0) is None


def test_dt9a2_a_real_zero_is_zero():
    assert europepmc_term_count("x", 90, lambda *a, **k: FakeResp(body={"hitCount": 0}), 1) == 0


def _run_checked(signed_in, ctx, replies, counts, papers=LONELY):
    """Run Discover with a model that answers `replies` in turn and a
    Europe PMC that answers `counts` (term -> int or None)."""
    llm = MagicMock()
    llm.generate.side_effect = list(replies)
    asked = []

    def counter(ctx_, settings):
        def count(term):
            asked.append(term)
            return counts.get(term, 5)
        return count

    body = {"description": "cooperative species survival", "api_key": "sk-x",
            "provider": "anthropic"}
    with patch.object(ctx, "get_orchestrator", return_value=FakeOrchestrator(papers=papers)), \
         patch("src.llm_providers.build_client", return_value=llm), \
         patch("web.routes_discover._europepmc_counter", counter):
        start = signed_in.post("/api/discover-terms", json=body)
        assert start.status_code == 202, start.text
        return _await(signed_in, start.json()["job_id"]), llm, asked


def test_dt9c_zero_hit_phrase_is_not_offered(signed_in, ctx):
    """Adversarial (P7): the owner's term. On-topic, finds nothing — never offered."""
    body, llm, _ = _run_checked(
        signed_in, ctx,
        [('{"terms": ["cooperative species survival", "cooperative breeding"]}', UNCOUNTED),
         ('{"terms": ["kin selection survival"]}', UNCOUNTED)],
        {"cooperative species survival": 0, "cooperative breeding": 1147,
         "kin selection survival": 0})
    r = body["result"]
    assert r["terms"] == ["cooperative breeding"]
    assert r["live_hits"] == {"cooperative breeding": 1147}
    assert [d["term"] for d in r["dropped"]] == ["cooperative species survival",
                                                  "kin selection survival"]
    assert r["dropped"][0]["reason"] == \
        "0 papers in Europe PMC with it in the title, abstract or keywords, last 90 days"


def test_dt9b_zero_terms_are_replaced_once(signed_in, ctx):
    body, llm, asked = _run_checked(
        signed_in, ctx,
        [('{"terms": ["cooperative species survival", "cooperation"]}', UNCOUNTED),
         ('{"terms": ["cooperative breeding", "Cooperation"]}', UNCOUNTED)],
        {"cooperative species survival": 0, "cooperation": 900, "cooperative breeding": 1147})
    assert llm.generate.call_count == 2                      # exactly one more call
    second_prompt = llm.generate.call_args_list[1].args[0]
    assert '"cooperative species survival"' in second_prompt
    assert "<papers>" in second_prompt                        # the papers again
    assert '"cooperation"' not in second_prompt.split("</papers>")[1]
    r = body["result"]
    assert r["terms"] == ["cooperation", "cooperative breeding"]   # duplicate skipped
    assert asked == ["cooperative species survival", "cooperation", "cooperative breeding"]


def test_dt9b_no_replacement_round_when_every_term_finds_papers(signed_in, ctx):
    body, llm, _ = _run_checked(signed_in, ctx, [('{"terms": ["a", "b"]}', UNCOUNTED)],
                                {"a": 3, "b": 4})
    assert llm.generate.call_count == 1
    assert body["result"]["dropped"] == []


def test_dt9b_two_model_calls_are_billed_as_one(signed_in, ctx):
    from src import user_store as us
    recorded = []
    with patch.object(us, "record_spend",
                      side_effect=lambda *a, **k: recorded.append(a[-1])):
        _run_checked(signed_in, ctx,
                     [('{"terms": ["dead"]}', TokenUsage(100, 10, 110, True)),
                      ('{"terms": ["alive"]}', TokenUsage(120, 5, 125, True))],
                     {"dead": 0, "alive": 7})
    assert recorded == [TokenUsage(220, 15, 235, True)]


def test_dt9b_failed_replacement_keeps_first_terms_and_their_cost(signed_in, ctx):
    from src import user_store as us
    from src.llm_providers import ProviderResponseError
    recorded = []
    err = ProviderResponseError("boom")
    with patch.object(us, "record_spend",
                      side_effect=lambda *a, **k: recorded.append(a[-1])):
        body, _, _ = _run_checked(signed_in, ctx,
                                  [('{"terms": ["dead", "alive"]}', TokenUsage(100, 10, 110, True)),
                                   err],
                                  {"dead": 0, "alive": 7})
    assert body["status"] == "done"
    assert body["result"]["terms"] == ["alive"]
    assert [d["term"] for d in body["result"]["dropped"]] == ["dead"]
    assert recorded and recorded[0].total == 110


def test_dt9c2_all_dropped_is_reported(signed_in, ctx):
    body, _, _ = _run_checked(signed_in, ctx,
                              [('{"terms": ["x y z"]}', UNCOUNTED), ('{"terms": ["p q r"]}', UNCOUNTED)],
                              {"x y z": 0, "p q r": 0})
    r = body["result"]
    assert r["terms"] == [] and len(r["dropped"]) == 2
    assert r["papers_found"] == 2                  # not the "no papers" case


def test_dt9d_unchecked_terms_are_kept_and_labelled(signed_in, ctx):
    """Europe PMC failing is not "found nothing" (P1): kept, null, no replacement."""
    body, llm, _ = _run_checked(signed_in, ctx, [('{"terms": ["a", "b"]}', UNCOUNTED)],
                                {"a": None, "b": 12})
    r = body["result"]
    assert r["terms"] == ["a", "b"]
    assert r["live_hits"] == {"a": None, "b": 12}
    assert r["dropped"] == []
    assert llm.generate.call_count == 1


def test_dt9f_phase_names_the_check():
    phases = []
    counts = check_terms(["a", "b", "c"], lambda t: len(t), 0,
                         lambda i, n: phases.append((i, n)))
    assert counts == {"a": 1, "b": 1, "c": 1}
    assert phases == [(1, 3), (2, 3), (3, 3)]


def test_dt9f_requests_are_spaced_by_the_configured_delay():
    with patch("src.discover.time.sleep") as sleep:
        check_terms(["a", "b", "c"], lambda t: 1, 0.25)
    assert [c.args[0] for c in sleep.call_args_list] == [0.25, 0.25]


def test_dt9g_check_settings_come_from_config(caplog):
    s = discover_settings({"discover": {"stop_words": ["the"], "system_prompt": "S",
                                        "replace_prompt": "R {terms}",
                                        "check_timeout_s": 4, "check_delay_s": 0.5}})
    assert (s.replace_prompt, s.check_timeout_s, s.check_delay_s) == ("R {terms}", 4.0, 0.5)
    with caplog.at_level("WARNING", logger="src.discover"):
        s = discover_settings({"discover": {"stop_words": ["the"], "system_prompt": "S",
                                            "replace_prompt": "no placeholder"}})
    assert "{terms}" in s.replace_prompt
    assert any("replace_prompt" in r.getMessage() for r in caplog.records)
    assert any("check_timeout_s" in r.getMessage() for r in caplog.records)


def test_dt9g_repo_config_has_check_settings():
    from src.llm_config import load_llm_config
    block = load_llm_config()["discover"]
    assert "{terms}" in block["replace_prompt"]
    assert block["check_timeout_s"] > 0 and block["check_delay_s"] >= 0


def test_dt9b_replace_prompt_lists_the_failed_terms():
    p = build_replace_prompt("FIRST", ["a b", "c"], "Replace {terms} please")
    assert p.startswith("FIRST") and 'Replace "a b"; "c" please' in p


def test_dt9b_replacement_that_never_reached_the_model_keeps_the_cost_counted(signed_in, ctx):
    """DT9 gate F1: an LLMError carrying UNCOUNTED never reached the model.
    Adding it would mark the first call's known 110 tokens "not reported"."""
    from src import user_store as us
    from src.llm_providers import ProviderUnavailableError
    recorded = []
    with patch.object(us, "record_spend",
                      side_effect=lambda *a, **k: recorded.append(a[-1])):
        _run_checked(signed_in, ctx,
                     [('{"terms": ["dead", "alive"]}', TokenUsage(100, 10, 110, True)),
                      ProviderUnavailableError("down")],
                     {"dead": 0, "alive": 7})
    assert recorded == [TokenUsage(100, 10, 110, True)]


def test_dt9b_replacement_that_cost_tokens_adds_them(signed_in, ctx):
    from src import user_store as us
    from src.llm_providers import ProviderResponseError
    recorded = []
    with patch.object(us, "record_spend",
                      side_effect=lambda *a, **k: recorded.append(a[-1])):
        _run_checked(signed_in, ctx,
                     [('{"terms": ["dead", "alive"]}', TokenUsage(100, 10, 110, True)),
                      ProviderResponseError("declined", usage=TokenUsage(50, 0, 50, True))],
                     {"dead": 0, "alive": 7})
    assert recorded == [TokenUsage(150, 10, 160, True)]


def test_dt9f_job_phase_shows_the_check(signed_in, ctx):
    """DT9 gate F2: the phase the job actually reported, not the source text."""
    body, _, _ = _run_checked(signed_in, ctx, [('{"terms": ["a", "b"]}', UNCOUNTED)],
                              {"a": 3, "b": 4})
    assert body["phase"] == "Checking terms in Europe PMC (2 of 2)"


def test_and6_and_term_counts_need_every_part():
    """docs/implementation_plan_2026-10-07_and_terms.md AND6."""
    papers = [{"title": "Cooperation in ants", "abstract": ""},
              {"title": "Survival of cooperative birds", "abstract": ""},
              {"title": "Survival", "abstract": "Cooperative breeding."}]
    hits = count_term_hits(["cooperati* AND survival", "cooperati* AND survival, ants",
                            "Cooperative AND Survival"], papers)
    assert hits == {"cooperati* AND survival": 2, "cooperati* AND survival, ants": 3,
                    "Cooperative AND Survival": 2}



def test_ta4_check_counts_titles_and_abstracts():
    """docs/implementation_plan_2026-10-07_title_abs.md TA4: the Discover
    check counts title/abstract matches, as the filter keeps, not full text."""
    sent = []
    europepmc_term_count("kin selection", 90,
                         lambda url, params, timeout: sent.append(params["query"])
                         or FakeResp(body={"hitCount": 5}), 1)
    assert 'TITLE_ABS:"kin selection"' in sent[0]
    assert ' "kin selection"' not in sent[0].replace('TITLE_ABS:"kin selection"', "")


def test_td4_td5_discover_counts_follow_the_filter():
    papers = [{"title": "Evidence for kin-selection in sharks", "abstract": ""},
              {"title": "Morning cortisol", "abstract": "Sleep was measured."}]
    hits = count_term_hits(["kin selection", "cortisol sleep", "cortisol AND sleep"], papers)
    assert hits == {"kin selection": 1, "cortisol sleep": 0, "cortisol AND sleep": 1}
