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

from src.tokens import UNCOUNTED

from src import user_store

PAPER = {"title": "Generative Agents", "abstract": "Interactive simulacra.",
         "doi": "10.1234/agents", "canonical_id": "arxiv:1"}
SUMMARY = {"key_findings": ["f"], "methodology": "m", "conclusions": "c"}


def _paper(n):
    """A distinct paper. Since review M26 a paper that already has a full-text
    summary gets it back free, so a test of the cap needs a new paper per run."""
    return dict(PAPER, doi=f"10.1234/agents-{n}", canonical_id=f"doi:10.1234/agents-{n}")



def _full_text(ctx, paper, outcome=None, by_title=None):
    """Stand-in for a found PDF: summaries now need full text, or the abstract
    is kept without calling the model (plan 2026-09-19 C1)."""
    if outcome is not None:
        outcome.update(full_text="used", text_source="Unpaywall")
    return "Full text of the paper: methods, results and discussion."

@pytest.fixture
def no_pdf():
    with patch("web.routes_summaries._extract_text", side_effect=_full_text):
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
    c.summarize_paper.return_value = (SUMMARY, UNCOUNTED)   # the (summary, usage) pair
    return c


def test_owner_key_summaries_are_capped_per_user_per_day(signed_in, owner_key, no_pdf):
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        for i in range(3):
            r = signed_in.post("/api/summaries", json={"paper": _paper(i)})
            assert r.status_code == 202, f"request {i} was refused early"
            _await(signed_in, r.json()["job_id"])

        refused = signed_in.post("/api/summaries", json={"paper": _paper(3)})

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
        for n in range(5):          # well past the cap of 3
            r = signed_in.post("/api/summaries", json={"paper": _paper(n)})
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
        for n in range(3):
            r = alice.post("/api/summaries", json={"paper": _paper(n)})
            _await(alice, r.json()["job_id"])
        assert alice.post("/api/summaries",
                          json={"paper": _paper(3)}).status_code == 429

        bob.post("/api/session", json=account_body(ACCESS_CODE))
        assert bob.post("/api/summaries", json={"paper": _paper(4)}).status_code == 202


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

    def fire(n):
        start.wait(timeout=5)
        # A different paper each: since review A7 a second run for the same
        # paper is answered 409, which would hide what this test measures.
        paper = dict(PAPER, doi=f"10.1/cap{n}", canonical_id=f"doi:10.1/cap{n}")
        r = client.post("/api/summaries", json={"paper": paper})
        with codes_lock:
            codes.append(r.status_code)

    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        threads = [threading.Thread(target=fire, args=(n,)) for n in range(8)]
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
            r = signed_in.post("/api/summaries", json={"paper": {
                "title": "t", "abstract": "", "doi": "10.1/no-text"}})
            _await(signed_in, r.json()["job_id"])

    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 3


def test_a1_a_paper_with_no_id_is_refused_and_its_slot_returned(ctx, signed_in, owner_key):
    """Review A1: a paper is looked up by DOI or id; one with neither is refused
    at the route, before any job, and the reserved slot is given back."""
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        r = signed_in.post("/api/summaries", json={"paper": {"title": "t", "abstract": "a"}})
    assert r.status_code == 400
    assert "no DOI or id" in r.json()["detail"]
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



def test_m26_a_reused_summary_costs_no_allowance(signed_in, owner_key, no_pdf):
    """The same paper again gets its stored summary back without a model call,
    so it must not use a slot of the day's allowance."""
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        for _ in range(5):
            r = signed_in.post("/api/summaries", json={"paper": PAPER})
            assert r.status_code == 202
            _await(signed_in, r.json()["job_id"])
    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 2


def test_a14_a_summary_cancelled_at_shutdown_gives_its_slot_back(ctx, signed_in, owner_key, no_pdf):
    """Review A14: on a redeploy, queued jobs are cancelled before they start.
    The allowance slot reserved for one must come back."""
    import threading
    from src.jobs import JobRegistry
    ctx.jobs = JobRegistry(lanes={"search": 1, "model": 1})
    gate = threading.Event()
    blocker = ctx.jobs.submit("review", "someone-else", lambda j: gate.wait(5))
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        r = signed_in.post("/api/summaries", json={"paper": _paper(9)})
    assert r.status_code == 202
    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 2   # reserved
    ctx.jobs.shutdown(wait=False)
    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and \
            signed_in.get("/api/me").json()["owner_summaries_remaining"] != 3:
        time.sleep(0.02)
    assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 3


def test_a7_duplicate_job_409(ctx, signed_in, owner_key):
    """Review A7: a second Summarize for a paper whose run is still going gets
    the running job back, and no second slot is spent."""
    import threading
    gate = threading.Event()

    def slow(_ctx, paper, outcome=None, by_title=None):
        gate.wait(5)
        return _full_text(_ctx, paper, outcome, by_title)

    with patch("web.routes_summaries._extract_text", side_effect=slow), \
         patch("src.llm_providers.build_client", return_value=_ok_client()):
        first = signed_in.post("/api/summaries", json={"paper": _paper(1)})
        second = signed_in.post("/api/summaries", json={"paper": _paper(1)})
        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["job_id"] == first.json()["job_id"]
        assert signed_in.get("/api/me").json()["owner_summaries_remaining"] == 2
        gate.set()
        _await(signed_in, first.json()["job_id"])



# ── Batch-4 gate findings: checks that come before admission ─────────────────

def _spend_the_allowance(signed_in):
    with patch("src.llm_providers.build_client", return_value=_ok_client()):
        for n in range(3):
            r = signed_in.post("/api/summaries", json={"paper": _paper(100 + n)})
            _await(signed_in, r.json()["job_id"])


def test_gate4_reuse_at_cap_is_free(signed_in, owner_key, no_pdf):
    """A paper that already has a full-text summary gets it back even when the
    day's allowance is used up (finding 1b)."""
    _spend_the_allowance(signed_in)
    client = MagicMock()
    with patch("src.llm_providers.build_client", return_value=client):
        r = signed_in.post("/api/summaries", json={"paper": _paper(100)})
        assert r.status_code == 202, r.text
        body = _await(signed_in, r.json()["job_id"])
    assert body["result"]["reused"] is True
    client.summarize_paper.assert_not_called()


def test_gate4_duplicate_at_cap_is_409(ctx, signed_in, owner_key):
    """A duplicate of a running paper is "already running", not "cap reached"
    (finding 1a)."""
    import threading
    gate = threading.Event()

    def slow(_ctx, paper, outcome=None, by_title=None):
        gate.wait(5)
        return _full_text(_ctx, paper, outcome, by_title)

    with patch("web.routes_summaries._extract_text", side_effect=slow), \
         patch("src.llm_providers.build_client", return_value=_ok_client()):
        first = [signed_in.post("/api/summaries", json={"paper": _paper(n)}) for n in range(3)]
        assert [r.status_code for r in first] == [202, 202, 202]     # allowance now used
        again = signed_in.post("/api/summaries", json={"paper": _paper(0)})
        assert again.status_code == 409 and again.json()["job_id"] == first[0].json()["job_id"]
        gate.set()
        for r in first:
            _await(signed_in, r.json()["job_id"])


def test_gate4_request_connection_released(ctx, signed_in, owner_key, no_pdf):
    """Finding 2: the request thread's connection is released after a submit."""
    released = []
    real = ctx.db.release
    with patch.object(ctx.db, "release", side_effect=lambda: released.append(1) or real()), \
         patch("src.llm_providers.build_client", return_value=_ok_client()):
        r = signed_in.post("/api/summaries", json={"paper": _paper(55)})
        _await(signed_in, r.json()["job_id"])
    assert len(released) >= 2        # the request thread and the worker thread


def test_gate5_connection_released_when_admission_fails(ctx, signed_in, owner_key, no_pdf):
    """Batch-5 finding 1: a 429 (admission refused) still releases the request
    thread's connection; so does a review 409."""
    _spend_the_allowance(signed_in)
    released = []
    real = ctx.db.release
    with patch.object(ctx.db, "release", side_effect=lambda: released.append(1) or real()):
        r = signed_in.post("/api/summaries", json={"paper": _paper(999)})
    assert r.status_code == 429
    assert released, "the refused request kept its connection"


def test_gate5_reuse_that_finds_nothing_says_so(ctx, signed_in, owner_key):
    """Batch-5 finding 2: admitted as a free reuse, but the worker's check finds
    no stored full-text summary — a plain message, not an AttributeError."""
    from src.jobs import JobStatus
    from web.routes_summaries import _NO_SPEND, _run_summary
    work = _run_summary(ctx, "u", {"doi": "10.1/gone", "canonical_id": ""}, _NO_SPEND, None)
    with patch("web.routes_summaries._extract_text", side_effect=_full_text), \
         patch("web.routes_summaries._PAPER_LOOKUP",
               lambda ref, cfg=None: {"doi": "10.1/gone", "title": "Gone", "abstract": "a"}):
        me = signed_in.get("/api/me").json()["user_id"]
        job = ctx.jobs.submit("summary", me, work)
        for _ in range(300):
            if job.status in (JobStatus.DONE, JobStatus.ERROR):
                break
            time.sleep(0.01)
    assert job.status == JobStatus.ERROR
    assert "changed while this ran" in job.error and "AttributeError" not in job.error
