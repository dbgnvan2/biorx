"""
Tests for M1.A — every provider reports what its call cost, and no caller
throws that away.

Spec: docs/implementation_plan_2026-09-20_references_batch.md#M1.A
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.llm_providers import AnthropicClient, DeepSeekClient
from src.llm import OllamaClient
from src.tokens import (UNCOUNTED, TokenUsage, from_anthropic, from_ollama,
                        from_openai_style)

SUMMARY_JSON = {"key_findings": ["f"], "methodology": "m", "conclusions": "c"}


def _ds_response(body, usage=None):
    """A DeepSeek (OpenAI-dialect) response body, with or without usage."""
    payload = {"choices": [{"message": {"content": body}}]}
    if usage is not None:
        payload["usage"] = usage
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = payload
    return resp


def _anthropic_message(body, usage=None):
    block = MagicMock()
    block.type = "text"
    block.text = body
    msg = MagicMock()
    msg.content = [block]
    msg.stop_reason = "end_turn"
    msg.usage = usage
    return msg


# ── M1.A.1: each provider's usage is read in its own dialect ──────────────────

def test_m1a1_each_provider_reports_its_usage():
    """The three dialects name these fields differently; each is read correctly
    and lands in the same TokenUsage shape."""
    deepseek = from_openai_style(
        {"usage": {"prompt_tokens": 1200, "completion_tokens": 300,
                   "total_tokens": 1500}}, "DeepSeek")
    assert (deepseek.prompt, deepseek.completion, deepseek.total) == (1200, 300, 1500)
    assert deepseek.counted

    anthropic = from_anthropic(_anthropic_message(
        "x", usage=MagicMock(input_tokens=1000, output_tokens=250,
                             cache_creation_input_tokens=0,
                             cache_read_input_tokens=0)))
    assert (anthropic.prompt, anthropic.completion, anthropic.total) == (1000, 250, 1250)
    assert anthropic.counted

    ollama = from_ollama({"prompt_eval_count": 800, "eval_count": 120})
    assert (ollama.prompt, ollama.completion, ollama.total) == (800, 120, 920)
    assert ollama.counted


def test_m1a1_anthropic_counts_cached_tokens_as_prompt_tokens():
    """input_tokens excludes tokens served from or written to the prompt cache.
    Counting only input_tokens would under-report a cached call — the same
    paper would look cheaper on a second run than it was."""
    usage = from_anthropic(_anthropic_message("x", usage=MagicMock(
        input_tokens=200, output_tokens=50,
        cache_creation_input_tokens=1000, cache_read_input_tokens=4000)))
    assert usage.prompt == 5200          # 200 + 1000 + 4000, not 200
    assert usage.total == 5250


@pytest.mark.parametrize("payload", [
    {},                                        # no usage block at all
    {"usage": None},                           # present but null
    {"usage": {}},                             # present but empty
    {"usage": {"prompt_tokens": None, "completion_tokens": None}},
    {"usage": {"prompt_tokens": "1200"}},      # a string, not a count
    {"usage": {"prompt_tokens": -5}},          # negative
    {"usage": {"prompt_tokens": True}},        # bool is an int subclass
])
def test_m1a1_missing_usage_is_not_zero(payload):
    """A provider that says nothing usable gives an UNCOUNTED result. This is
    the distinction the whole module exists for: `counted=False` means unknown.
    A counted zero would be a false record, and a meter adding it would
    under-report silently (P2)."""
    usage = from_openai_style(payload, "DeepSeek")
    assert usage.counted is False
    assert (usage.prompt, usage.completion, usage.total) == (0, 0, 0)


def test_m1a1_ollama_without_counts_is_uncounted():
    """Ollama omits these on some model/version combinations."""
    assert from_ollama({"response": "text"}).counted is False
    assert from_anthropic(_anthropic_message("x", usage=None)).counted is False


def test_m1a1_a_partial_report_is_still_counted():
    """One of the two counts present is a real, if incomplete, report — better
    recorded as what it is than discarded."""
    usage = from_openai_style({"usage": {"prompt_tokens": 900}}, "DeepSeek")
    assert usage.counted and usage.prompt == 900 and usage.completion == 0


def test_m1a1_summing_an_uncounted_call_does_not_look_complete():
    """Adding an unknown to a known total must not present the sum as the whole
    truth, or a batch containing one uncounted call reads as fully measured."""
    known = TokenUsage(prompt=100, completion=10, total=110, counted=True)
    assert (known + known).counted is True
    assert (known + known).total == 220
    assert (known + UNCOUNTED).counted is False
    assert (known + UNCOUNTED).total == 110      # the known part is still kept


# ── M1.A.1: the clients return the pair, end to end ───────────────────────────

def test_m1a1_deepseek_client_returns_text_and_usage():
    client = DeepSeekClient(api_key="k")
    response = _ds_response(json.dumps(SUMMARY_JSON),
                            usage={"prompt_tokens": 7000, "completion_tokens": 400,
                                   "total_tokens": 7400})
    with patch("src.llm_providers.requests.post", return_value=response):
        summary, usage = client.summarize_paper("abstract", "text")
    assert summary["conclusions"] == "c"
    assert (usage.prompt, usage.completion, usage.counted) == (7000, 400, True)


def test_m1a1_anthropic_client_returns_text_and_usage():
    client = AnthropicClient(api_key="k")
    sdk = MagicMock()
    sdk.messages.create.return_value = _anthropic_message(
        json.dumps(SUMMARY_JSON),
        usage=MagicMock(input_tokens=6000, output_tokens=350,
                        cache_creation_input_tokens=0, cache_read_input_tokens=0))
    with patch.object(AnthropicClient, "_client", return_value=sdk):
        summary, usage = client.summarize_paper("abstract", "text")
    assert summary["conclusions"] == "c"
    assert (usage.prompt, usage.completion, usage.counted) == (6000, 350, True)


def test_m1a1_ollama_client_returns_text_and_usage():
    client = OllamaClient()
    resp = MagicMock()
    resp.json.return_value = {"response": "hello", "prompt_eval_count": 500,
                              "eval_count": 60}
    resp.raise_for_status.return_value = None
    with patch("src.llm.requests.post", return_value=resp):
        text, usage = client.generate("prompt")
    assert text == "hello"
    assert (usage.prompt, usage.completion, usage.counted) == (500, 60, True)


def test_m1a1_a_failed_ollama_call_is_uncounted_not_free():
    """The request never reached the model, so there is nothing to report —
    but the caller must still get the pair, not a bare None."""
    import requests as _requests
    with patch("src.llm.requests.post", side_effect=_requests.RequestException("down")):
        text, usage = OllamaClient().generate("prompt")
    assert text is None and usage.counted is False


def test_m1b3_ollama_records_tokens_even_when_the_reply_cannot_be_parsed():
    """The model ran and was billed. An unparseable reply loses the summary,
    never the record of what it cost (M1.B.3)."""
    resp = MagicMock()
    resp.json.return_value = {"response": "not in the expected shape at all",
                              "prompt_eval_count": 400, "eval_count": 30}
    resp.raise_for_status.return_value = None
    with patch("src.llm.requests.post", return_value=resp):
        summary, usage = OllamaClient().summarize_paper("abstract", "text")
    assert usage.counted and usage.prompt == 400


# ── M1.A.2: no call site drops the usage ──────────────────────────────────────

_CALLERS = [
    ("src/llm_providers.py", r"self\.generate\("),
    ("src/llm.py", r"self\.generate\("),
    ("web/routes_discover.py", r"\.generate\("),
    ("web/routes_summaries.py", r"\.summarize_paper\("),
    ("agents/summarization_agent.py", r"\.summarize_paper\("),
    ("gui.py", r"\.generate\("),
]


@pytest.mark.parametrize("path,pattern", _CALLERS)
def test_m1a2_no_caller_discards_usage(path, pattern):
    """Every call site unpacks both halves of the pair.

    Without this, adding a call site that writes `text = client.generate(...)`
    silently binds a tuple and the cost is never recorded — the failure mode
    the pair-return exists to prevent, and one no runtime test would catch
    because the code still 'works' (P25).
    """
    source = (Path(__file__).parent.parent / path).read_text()
    calls = [line.strip() for line in source.splitlines()
             if re.search(pattern, line) and "def " not in line
             and not line.strip().startswith("#")]
    assert calls, f"no call site found in {path} — did the pattern go stale?"
    for line in calls:
        assert re.search(r"^\s*\w+\s*,\s*\w+\s*=", line), (
            f"{path}: this call drops the token usage: {line}"
        )


def test_m1a2_every_provider_client_returns_a_pair():
    """A fourth provider added later must return the pair too. Asserting on the
    annotation catches the one that does not, before it ships."""
    import inspect
    from src.llm import MockOllamaClient

    for cls in (DeepSeekClient, AnthropicClient, OllamaClient, MockOllamaClient):
        for method in ("generate", "summarize_paper"):
            fn = getattr(cls, method, None)
            if fn is None or method not in cls.__dict__:
                continue          # inherited; checked on the class that defines it
            annotation = inspect.signature(fn).return_annotation
            assert "Tuple" in str(annotation), (
                f"{cls.__name__}.{method} returns {annotation}, not a "
                "(value, TokenUsage) pair"
            )


# ── M1.B: the usage log records what each call cost ───────────────────────────

def _db(tmp_path):
    from src.db import Database
    return Database(str(tmp_path / "t.db"))


def _columns(db, table):
    return {r[1] for r in db.conn.execute(f"PRAGMA table_info({table})")}


def test_m1b1_migration_is_additive_and_idempotent(tmp_path):
    """The columns arrive without disturbing rows written before them, and a
    second startup does not fail or duplicate them. Railway restarts the app
    on every deploy, so this runs against a populated database every time."""
    from src.db import Database

    path = str(tmp_path / "m.db")
    db = Database(path)
    # A row from "before" the migration: strip the new columns back off by
    # writing one with only the original fields.
    db.conn.execute(
        "INSERT INTO usage_events (user_id, kind, provider, model, key_source) "
        "VALUES ('u1', 'summary', 'deepseek', 'deepseek-flash', 'owner')")
    db.conn.commit()
    db.close()

    db = Database(path)                       # migration runs again on open
    cols = _columns(db, "usage_events")
    assert {"prompt_tokens", "completion_tokens", "tokens_counted"} <= cols

    rows = db.conn.execute("SELECT * FROM usage_events").fetchall()
    assert len(rows) == 1, "the migration must not duplicate or drop rows"
    old = dict(rows[0])
    assert old["user_id"] == "u1" and old["key_source"] == "owner"
    # The decisive point: a pre-migration row reads as UNCOUNTED, not as a
    # call that cost nothing.
    assert old["tokens_counted"] == 0
    db.close()


def test_m1b2_owner_run_updates_the_reserved_row_not_a_new_one(tmp_path):
    """The cap reserves a row up front. Recording tokens must fill that row in.
    Inserting a second would make one summary eat two of the day's slots."""
    from src import user_store

    db = _db(tmp_path)
    usage_id = user_store.reserve_owner_usage(db, "u1", "summary", cap=3,
                                              provider="deepseek", model="m")
    assert usage_id is not None
    before = user_store.owner_usage_today(db, "u1")

    user_store.finalize_usage(db, usage_id, "deepseek", "deepseek-flash",
                              TokenUsage(prompt=7000, completion=400,
                                         total=7400, counted=True))

    rows = db.conn.execute("SELECT * FROM usage_events").fetchall()
    assert len(rows) == 1, "a second row would be double-counted by the cap"
    assert dict(rows[0])["prompt_tokens"] == 7000
    assert dict(rows[0])["tokens_counted"] == 1
    assert user_store.owner_usage_today(db, "u1") == before == 1
    db.close()


def test_m1b2_user_key_run_is_recorded_without_consuming_the_cap(tmp_path):
    """A user on their own key is not capped, but their spend must still show
    in the meter. The row is written with key_source='user', which the cap's
    count ignores."""
    from src import user_store

    db = _db(tmp_path)
    user_store.record_usage(db, "u1", "summary", "deepseek", "deepseek-flash",
                            "user", TokenUsage(prompt=500, completion=50,
                                               total=550, counted=True))
    assert user_store.owner_usage_today(db, "u1") == 0      # cap untouched
    totals = user_store.session_token_totals(db, "u1", datetime(1970, 1, 1))
    assert totals["total"] == 550 and totals["counted_calls"] == 1
    db.close()


def test_m1b2_an_uncounted_call_is_logged_but_adds_nothing(tmp_path):
    """An Ollama run that reported no counts is a real call with an unknown
    cost. It appears in the log and in the uncounted tally, and contributes
    zero to the total rather than being dropped from the record (P2)."""
    from src import user_store

    db = _db(tmp_path)
    user_store.record_usage(db, "u1", "summary", "ollama", "qwen3.5:4b", "none")
    totals = user_store.session_token_totals(db, "u1", datetime(1970, 1, 1))
    assert totals["total"] == 0
    assert totals["counted_calls"] == 0 and totals["uncounted_calls"] == 1
    db.close()


def test_m1c1_totals_cover_only_this_user_and_this_window(tmp_path):
    """Another user's spend is never in your meter, and neither is spend from
    before the window."""
    from src import user_store

    db = _db(tmp_path)
    counted = TokenUsage(prompt=100, completion=10, total=110, counted=True)
    user_store.record_usage(db, "u1", "summary", "d", "m", "user", counted)
    user_store.record_usage(db, "u2", "summary", "d", "m", "user", counted)
    db.conn.execute("UPDATE usage_events SET created_at = '2000-01-01 00:00:00' "
                    "WHERE user_id = 'u1'")
    db.conn.commit()
    user_store.record_usage(db, "u1", "summary", "d", "m", "user", counted)

    totals = user_store.session_token_totals(db, "u1", datetime(2020, 1, 1))
    assert totals["total"] == 110, "only u1's in-window call counts"
    db.close()


def test_m1c1_since_is_formatted_to_match_how_sqlite_stores_created_at(tmp_path):
    """Gate finding 4: created_at is written by CURRENT_TIMESTAMP as
    "YYYY-MM-DD HH:MM:SS". An ISO-8601 string sorts above every stored value
    ("T" > " "), so passing one would return zero for everything — a meter
    reading 0 while the user was in fact spending. The function takes a
    datetime so a caller cannot make that mistake, and converts tz-aware values
    to UTC so a local-time stamp cannot shift the window.
    """
    from src import user_store

    db = _db(tmp_path)
    user_store.record_usage(db, "u1", "summary", "d", "m", "user",
                            TokenUsage(prompt=10, completion=1, total=11, counted=True))

    naive = user_store.session_token_totals(db, "u1", datetime(2020, 1, 1))
    aware = user_store.session_token_totals(
        db, "u1", datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert naive["total"] == aware["total"] == 11
    assert " " in user_store._sqlite_timestamp(datetime(2026, 9, 20, 12, 0, 0))
    assert "T" not in user_store._sqlite_timestamp(datetime(2026, 9, 20, 12, 0, 0))
    db.close()


# ── Gate findings: spend that used to go unrecorded ───────────────────────────

def test_gate1_hosted_provider_keeps_the_usage_when_the_reply_will_not_parse(tmp_path):
    """Gate finding 2: a hosted provider bills for a reply whose content turns
    out to be unusable. The response reported what it cost; letting the parse
    error escape bare threw that away, so a call that spent real money was
    recorded as costing nothing.

    This is the case M1.B.3 claimed to cover and only covered for Ollama.
    """
    from src.llm_providers import ProviderResponseError

    billed = {"prompt_tokens": 6000, "completion_tokens": 200, "total_tokens": 6200}
    response = _ds_response("this is not JSON at all", usage=billed)
    with patch("src.llm_providers.requests.post", return_value=response):
        with pytest.raises(ProviderResponseError) as caught:
            DeepSeekClient(api_key="k").summarize_paper("a", "t")
    assert caught.value.usage.counted is True
    assert caught.value.usage.prompt == 6000


def test_gate1_an_empty_summary_also_keeps_its_usage():
    """The other unusable-reply branch: valid JSON, no content in it."""
    from src.llm_providers import ProviderResponseError

    billed = {"prompt_tokens": 900, "completion_tokens": 20, "total_tokens": 920}
    empty = json.dumps({"key_findings": [], "methodology": "", "conclusions": ""})
    with patch("src.llm_providers.requests.post",
               return_value=_ds_response(empty, usage=billed)):
        with pytest.raises(ProviderResponseError) as caught:
            DeepSeekClient(api_key="k").summarize_paper("a", "t")
    assert caught.value.usage.prompt == 900


def test_gate1_anthropic_refusal_keeps_its_usage():
    """A declined request is still billed for what it read."""
    from src.llm_providers import ProviderResponseError

    msg = _anthropic_message("", usage=MagicMock(
        input_tokens=4000, output_tokens=5,
        cache_creation_input_tokens=0, cache_read_input_tokens=0))
    msg.stop_reason = "refusal"
    sdk = MagicMock()
    sdk.messages.create.return_value = msg
    with patch.object(AnthropicClient, "_client", return_value=sdk):
        with pytest.raises(ProviderResponseError) as caught:
            AnthropicClient(api_key="k").summarize_paper("a", "t")
    assert caught.value.usage.prompt == 4000


def test_gate1_a_call_that_never_reached_the_model_carries_no_usage():
    """The mirror case — an error raised before any request must not claim a
    cost. UNCOUNTED, not a fabricated number."""
    from src.llm_providers import NoLLMCredentialError

    with pytest.raises(NoLLMCredentialError) as caught:
        DeepSeekClient(api_key="").summarize_paper("a", "t")
    assert caught.value.usage.counted is False


def test_gate2_both_spending_routes_use_the_shared_recorder():
    """Gate finding 1: the summaries route recorded tokens; its sibling the
    discover route settled the owner's slot and recorded nothing, so discover
    spend never reached the meter — and a user-key discover run wrote no row
    at all.

    Both now go through user_store.record_spend. A source check, because the
    two routes drifting apart is precisely what happened and neither route's
    own tests noticed (P5).
    """
    for path in ("web/routes_summaries.py", "web/routes_discover.py"):
        source = (Path(__file__).parent.parent / path).read_text()
        assert "user_store.record_spend(" in source, (
            f"{path} does not use the shared recorder — it will drift again"
        )
        # The private per-route helper this replaced must not come back.
        assert "def _record_spend" not in source
