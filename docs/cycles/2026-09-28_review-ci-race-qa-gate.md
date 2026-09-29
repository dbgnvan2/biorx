# Re-gate — CI race fix, Learning-QA gate (2026-09-28)

One test-only commit after the batch-8 gates approved. CI failed intermittently
(on and off from review batch 2 through batch 7): a web test returned while its
background summary job still ran, the stub fixture was undone, and the job then
reached the real network. This commit adds a `settle_jobs` helper and calls it at
every site that fires a job inside a stub without awaiting it.

RANGE:     origin/main..HEAD (three-dot vs merge base 018c1a6)
COMMITS:   1 — 7805ac4 "test: no background job outlives the stub it depends on"
APPLICABLE: generic P5 (sibling calls — the settle must cover EVERY test that
           fires a job inside a stub), P15 (background worker must reach a
           terminal status, which settle_jobs waits on), P26 (this is itself a
           fix commit, swept here), P28/P34 (test isolation — the network guard
           and real-db guard are the safety net, not weakened).
CHECKED:    the helper against JobRegistry's real internals (src/jobs.py); every
           POST /api/summaries and POST /api/reviews call site across
           tests/web/ for the "fires a job, never awaits" shape; fixture
           teardown ordering; CI failure logs (gh run view --log-failed); the
           full suite.
NOT COVERED: search/discover jobs (SEARCH_LANE) — a separate lane whose tests
           stub source adapters and await via their own helpers, and which no CI
           failure implicated; logic correctness outside the test layer (no
           src/ or web/ change in this commit).

## The failure, reproduced from CI

`gh run view 36518428169 --log-failed` (and the batch-7 run 36517249200) show
the same single error, intermittently:

    ERROR tests/web/test_cap.py::test_one_users_spending_does_not_cap_another
      - Failed: this test tried to reach the network, and something caught the
        guard's exception: ['104.26.4.132']. Stub the call.

Traceback: the summary job's worker (`src/jobs.py::_run`) → `summarize_paper` →
`find_text` → the REAL `_extract_text` → `find_full_text` → `download_pdf_text` →
`safe_fetch.fetch_pdf` → `socket.getaddrinfo('104.26.4.132')`, where the suite-wide
network guard (`tests/conftest.py::_guarded_getaddrinfo`) raised
`NetworkAccessInTest`. The job's broad `except` caught it (logged "Job … failed"),
and the teardown guard (`_fail_the_test_that_touched_the_network`) failed the test.

Root cause, exactly as the commit says: `test_one_users_spending_does_not_cap_another`
fires bob's summary request and asserts 202 without awaiting it, then exits its
`with patch("src.llm_providers.build_client")` block; the `no_pdf` fixture's
`with patch("web.routes_summaries._extract_text")` is undone at teardown. The job is
still running at that point, so `_extract_text` is real when the worker reaches it,
and the paper's DOI resolves to a real publisher URL → network → guard trips.

## The helper

`tests/web/conftest.py::settle_jobs(ctx, timeout=10.0)` polls `ctx.jobs._jobs`
under `ctx.jobs._lock` until every job's `status` is in `TERMINAL`
(`DONE|ERROR|CANCELLED`, imported from `src.jobs`), sleeping 10 ms per pass, and
raises `AssertionError` if jobs are still busy after `timeout`. Correct for the
task: a job reaches a terminal status only after its `work(job)` returns or raises
(src/jobs.py `_run`, guarded per P15), and `work(job)` is where the network calls
(`_extract_text`, `build_client().summarize_paper`/`generate`) happen — so terminal
status guarantees the network-using part is already done. Reaching into `_jobs`/`_lock`
privates is acceptable in test code, and mirrors the existing drain loop in
`JobRegistry.shutdown()` (which this helper correctly does NOT reuse: shutdown
cancels jobs, a side effect settle_jobs must not have, and it returns bool rather
than raising).

## Call-site coverage — the P5 class, checked exhaustively

The race class is "a test that submits a summary/review job inside a stub and does
not await it before the stub exits". Every such site is now settled. Enumerated
against the full files:

tests/web/test_cap.py — 3 non-awaited sites, all settled:
  test_the_cap_counts_only_owner_key_usage           settle_jobs inside build_client patch
  test_one_users_spending_does_not_cap_another       settle_jobs inside build_client patch (the CI failure site)
  test_the_cap_holds_against_simultaneous_requests   settle_jobs inside build_client patch
  Every other job-starting test here awaits via `_await` inside its patch block, or
  the request is refused (429/400) before any job starts.

tests/web/test_summaries_routes.py — 1 non-awaited site, settled:
  test_a_normal_sized_paper_is_accepted              settle_jobs inside build_client patch
  All others await via `_await`.

tests/web/test_review_routes.py — 1 non-awaited site, settled:
  test_regate1_a_preview_reserves_no_allowance_on_the_shared_key  settle_jobs inside build_client patch
  All others use `_run`/`_await` (the `_run` helper awaits).

tests/web/test_summary_fetch_guard.py — 1 non-awaited site, settled:
  test_sum3_summary_job_passes_the_guarded_fetcher   settle_jobs inside build_client patch

Fixture teardown (belt-and-suspenders for the `_extract_text` stub itself):
  test_cap.py::no_pdf and test_summaries_routes.py::no_pdf/with_full_text now call
  settle_jobs(ctx) after `yield` and before their `with patch("..._extract_text")`
  block exits. This drains any job before the PDF stub is undone, directly closing
  the observed failure mode (real PDF download). Fixture teardown order is correct:
  `no_pdf` depends on `ctx`, so it is torn down BEFORE `ctx`'s teardown runs
  `jobs.shutdown(wait=True)` — settle_jobs sees a still-active registry.

Sibling files cross-checked for the same shape (all clean):
  test_spend.py       posts /api/summaries|reviews only in a no-key 400 test (no job).
  test_usage_routes.py  test_m1c1… awaits via `_await` inside its patch block.
  test_llm_key_routes.py  the one /api/summaries post returns 503 before any job.
  test_auth.py, test_app.py  no summary/review job submission.
  test_frontend_wiring.py  JS/node fake-fetch tests; no real background jobs.
  test_searches_routes.py / test_discover_routes.py / test_jobs.py  search/discover
    lane — out of this commit's scope (see NOT COVERED).

## Nothing weakened

The diff is +38/−5, tests-only (five files under tests/web/), no src/ or web/ change.
No assertion was removed or relaxed: the touched tests still assert the same exact
values (202/429/400, `accepted == 3`, `refused == 5`, `seen.get("fetch_html") is
safe_fetch.fetch_html`, etc.). The suite-wide network guard (`_no_network_in_tests`)
and the real-database fingerprint guard (`_real_database_is_never_touched`) are
untouched — they remain the hard backstop that turns any future re-introduction of
the race into a red run rather than a silent network call. The helper's 10 s timeout
raising AssertionError means a job that fails to drain fails loudly, never silently.

## Test suite

`venv/bin/python -m pytest tests/ -q` → 1567 passed, 2 skipped, 4 warnings (70.79 s).
Same count as the batch-8 re-gate (which added test_t2_*), so this commit changes no
test count — consistent with a helper + call-site-only change. Skips are the
CI-only checks (RLIMIT_AS and the Python-version guard).

## FINDINGS (ranked)

None.

Two minor observations, neither a finding (no caller hits them, and neither is
reachable from the default 10.0 s timeout):

1. `settle_jobs` references `busy` in the `raise` after the loop; the loop always
   runs at least once for the default `timeout=10.0`, but a `timeout <= 0` would
   `NameError` instead of the intended AssertionError. Hardening (initialize
   `busy = []` first) would be tidy but is not required by any call site.
2. The helper reaches into `JobRegistry` privates (`_lock`, `_jobs`). Correct for a
   test-only helper, and it must not reuse `shutdown()` (which cancels jobs as a
   side effect). A future JobRegistry refactor of those names would need to update
   the helper — acceptable coupling within one repo's test suite.

VERDICT: clean against the patterns examined (P5, P15, P26, P28/P34), of which P5
and P15 were applicable to the fix — with 0 findings. The settle_jobs helper and its
call sites close the race for every summary/review test that fires a job inside a
stub, the teardown ordering is right, and nothing was weakened.

**APPROVED**
