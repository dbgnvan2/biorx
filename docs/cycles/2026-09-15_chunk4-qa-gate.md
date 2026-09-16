# QA gate — chunk 4 (FastAPI backend), 2026-09-15

RANGE: origin/main..HEAD (commit f068285)
COMMITS: 1 — f068285 feat(web): FastAPI backend — access-code gate, async searches, summaries, cap
METHOD: adversarial security review of the materialized diff plus the current tree,
  standards at ~/.claude/standards/{learnings,security,external-api,llm-integration}.md,
  plan at docs/implementation_plan_2026-09-15.md.
TESTS: /opt/homebrew/bin/pytest tests/ -q -> 364 passed, 8 skipped, 1 warning, exit code 0.
VERDICT: REJECTED

APPLICABLE (of P1-P35): P2, P6, P8, P9, P15, P19, P22, P26, P27, P28, P29, P30, P34
  (most handled; see findings).
CHECKED:
  - ACCESS_CODE gate on every route (enumerated via app.openapi(), not a hand list).
  - Cookie signature / tamper / expiry / wrong-secret / deleted-user rejection.
  - Per-user scoping of filters, searches, summaries (all queries keyed on user_id/owner).
  - Key & ciphertext never in a response body, log line, job.error, or traceback.
  - Fernet at rest, secret required (never generated), ciphertext != plaintext.
  - Worker P15 guard (error status on any exception, incl. BaseException).
  - Job ownership: cross-user read/cancel/expiry all return UNKNOWN/404 (no oracle).
  - Tests in tests/web/: no test body lacks a failure path (pytest.raises tests fail
    when the exception is absent; "return" hits are nested closures, not test bodies;
    zero PytestReturnNotNoneWarning).
  - No key-shaped literal committed in history (git grep across all revisions); test
    keys are synthetic; .env* gitignored.
NOT COVERED:
  - Live provider calls (DeepSeek/Anthropic) are mocked; real HTTP is integration-only.
  - The SPA (web/static) does not exist yet; phase 9 will add GET / and /static.
  - Uvicorn multi-worker behaviour (per-process SESSION_SECRET invalidates sessions
    across workers) is documented, not tested.

RANKED FINDINGS
===============

F1 (HIGH, CONFIRMED BY CONSTRUCTION) — owner-key spend cap is a check-then-act race.
  File: web/routes_summaries.py:55-65 (check) vs :122-123 (record).
  The cap is read at submission (owner_usage_today) but the usage row is written only
  after the summary job completes. A burst of submissions all observe used=0 and pass.
  Proof (isolated app, cap=3, 6 rapid requests, workers held open):
      accepted(202)=6  refused(429)=0  -> BYPASS CONFIRMED
  Impact: §1.4's entire purpose ("without a ceiling anyone holding the code can spend
  the owner's credential without limit") is restored by firing requests faster than a
  job completes. Unbounded owner-credential spend.
  Fix: reserve the budget atomically at admission — e.g. an atomic INSERT/COUNT in the
  same transaction that fails (and 429s) once committed owner-key usage >= cap, or a
  per-user in-process counter (lock-guarded) incremented before submit. Record-usage
  must move to admission, not completion, or the check must count queued+running jobs.
  Confidence: high (reproduced end-to-end with the real route + registry).

F2 (MEDIUM) — user-supplied abstract is not length-capped before the LLM call.
  File: web/routes_summaries.py:94 + src/llm_providers.py:104-109.
  _build_summary_prompt truncates full_text (_truncate) but not abstract; SummaryRequest
  accepts an arbitrary Dict. A request can carry a multi-megabyte abstract straight into
  the owner-billed prompt, so a single request burns unbounded owner tokens even though
  the cap counts "summaries", not tokens.
  Impact: second cost-control gap on the owner credential; also a prompt-size DoS.
  Fix: enforce a named abstract budget (reuse max_text_chars or a dedicated
  MAX_ABSTRACT_CHARS) and announce the drop (L7/P9); add a pydantic max_length on the
  paper fields or a body-size ceiling.
  Confidence: high (path is user-controlled end-to-end).

F3 (LOW) — unbounded job queue and user creation.
  File: src/jobs.py (submit has no global/per-user cap); web/routes_session.py:88
  (create_session mints a new user row per POST, never re-associates).
  A holder of the code can flood the registry with queued jobs (memory/CPU) and grow the
  users table without bound.
  Fix: per-user concurrency/lifetime quota on submit; dedupe or re-use session identity.
  Confidence: medium (resource exhaustion, not credential access).

F4 (LOW, ACCEPTED DESIGN — flag for the record) — shared summary endpoint leaks provenance.
  File: web/routes_summaries.py:181-189 returns get_summary(paper_id), which is SELECT *
  including created_by_user_id. Any authenticated user can read every summary (decision
  §2.5: one shared summary per paper) and sees which user id produced the current one.
  Impact: minimal — user_id is opaque and unusable without a signed cookie, and the
  sharing is the approved design.
  Fix (optional): strip created_by_user_id from the response.
  Confidence: high that it happens; low that it matters.

F5 (LOW, TEST QUALITY) — route-enumeration floor + forward-compat.
  File: tests/web/test_auth.py:38-58. The per-route 401 assertion enumerates openapi()
  exhaustively (correct), but the sanity check `assert checked >= 10` is a floor (P29).
  When phase 9 lands, GET / will enter openapi() and return 200, failing the loop unless
  "/" is added to PUBLIC. This is a correct-but-fragile guard, not a current hole.
  Fix: assert an exact count of protected routes (or add / and /static to PUBLIC now, and
  assert membership rather than a cardinality floor).
  Confidence: high.

F6 (LOW, TEST QUALITY) — constant-time compare asserted via source inspection.
  File: tests/web/test_auth.py:160-171. AST-walks check_access_code's source for the name
  "compare_digest". It catches replacing the compare with `==` but would pass if the name
  were referenced but not called. The behavioural property (constant-time) is untested.
  Fix: acceptable as a tripwire, but prefer asserting against a known-good reference
  implementation of the timing behaviour, or at minimum assert the call site exists in
  the AST (not just the name).
  Confidence: medium (narrow gap).

VERDICT RATIONALE
  Two findings are medium-or-higher and security-relevant: F1 is a constructed bypass of
  the chunk's headline control (the owner-key spend cap), and F2 is an unbounded
  owner-token vector. Per the loop's stop condition ("stop when a pass produces nothing of
  medium or higher"), this chunk is not approved. F3-F6 go to the backlog.

---

# FIX-LOOP 1 — re-gate on origin/main..HEAD (commits f068285, 5e867f2), 2026-09-15

RANGE: origin/main..HEAD
COMMITS: 2 — f068285 (FEATURE, previously rejected) + 5e867f2 (FIX)
METHOD: adversarial verification by construction, not reading. Materialized diff plus live
  probes against temp databases and an isolated FastAPI app; the suite hammered for the
  reported SIGSEGV flakiness. Standards as in the first pass.
TESTS: 5 full-suite runs -> 373 passed, 8 skipped, exit code 0 every time (no SIGSEGV).
VERDICT: APPROVED

F1 (was HIGH) — RESOLVED. user_store.reserve_owner_usage reserves the slot at admission with a
  single atomic INSERT...SELECT...WHERE (SELECT COUNT(*)...) < cap, not a read-then-write.
  Verified by construction:
  - same user, 8 separate Database connections, cap=3, 50 iterations -> exactly 3 admitted,
    zero overshoot, zero exceptions.
  - cap=1, 4 threads, 30 iterations -> exactly 1.
  - one Database (per-thread conns), 8 threads, 30 iterations -> exactly 3.
  - truly concurrent cross-process burst (8 procs, start-file barrier, one file, cap=3)
    -> exactly 3 OK / 5 NONE, 3 committed rows.
  - 24h boundary: 23h-old owner rows count, 25h-old do not; old rows never leak across users;
    the >= comparison is inclusive with no under-count.
  The original bypass (cap=3, six rapid requests, six accepted) is inverted: the suite's
  test_the_cap_holds_against_simultaneous_requests asserts accepted==3 / refused==5 and passes.

F2 (was MEDIUM) — RESOLVED. The abstract now has its own named budget
  (ABSTRACT_BUDGET_FRACTION=0.25, min 2000) applied inside the shared _build_summary_prompt —
  a single source of truth both hosted clients call, not a parallel copy — and the route
  enforces MAX_PAPER_BYTES=200_000 on json.dumps(body.paper) -> 413.
  Verified: a 5MB abstract is truncated to the budget in the prompt; a >200KB body is refused
  413; an under-ceiling body carrying a 100k-char abstract is accepted 202 but still truncated
  in the prompt. No field other than abstract/full_text reaches the prompt, and full_text (PDF)
  is separately truncated to the text budget — no owner-token bypass.

SIGSEGV fix — RESOLVED. JobRegistry.shutdown() now cancels, drains with a bounded wait, and
  returns whether it succeeded; the lifespan handler and the test ctx fixture close Database
  only when it returns True. Verified: a stuck worker makes shutdown return False in bounded
  time (no deadlock); unblocking drains True; a fast job drains True.

(d) release-on-early-failure — no free-summary path. Release fires only when the provider was
  never called (provider_called is set immediately before the client call). Verified by
  construction: a produced summary consumes its slot; early failures (no abstract) give the
  slot back but never call the provider and never store a summary. No path reaches a summary
  with the slot released.

RESIDUAL (non-blocking, recorded):
- After shutdown() returns False, the interpreter can still wait at exit for a non-daemon
  ThreadPoolExecutor worker blocked without a timeout. No real worker path has that: provider
  calls timeout at 120s and PDF download at 30s, so worst-case exit delay is bounded (~150s),
  a delay not a hang. Nothing to change now; worth revisiting if a timeout-less blocking call
  is ever added to a worker.

VERDICT RATIONALE
  Both medium-or-higher findings are resolved and re-verified by construction; the shutdown
  drain is deadlock-free and the SIGSEGV is gone; releasing on early failure yields no free
  summaries. Full suite green across five runs. APPROVED.

