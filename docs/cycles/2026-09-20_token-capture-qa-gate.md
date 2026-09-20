# QA Gate — Token capture (M1.A / M1.B) + PDF naming

- **Date:** 2026-09-20
- **Range reviewed:** `a32138e..622ee9f` (4 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep (warm + one cold pass)
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1132 passed, 1 skipped**

## Commits

| sha | subject |
|---|---|
| fb53149 | fix(web): name a downloaded PDF after the paper, not its row id |
| d3c8cb4 | docs: implementation plan for the References batch and token tracking |
| b3b2732 | feat(tokens): capture the usage every provider already reports (M1.A) |
| 622ee9f | feat(tokens): store what each call cost in the usage log (M1.B) |

## Verdict: REJECTED

Two high-confidence findings leave real billed LLM spend unrecorded in
`usage_events` — the exact "failure that hides" this change exists to fix. Both
are incomplete application of M1.B across sibling call paths, not a cap bypass
or a crash. The owner-key spend cap itself is intact.

## Concerns checked

- **(a) Spend cap (`_resolve_for`) bypass / double-count** — SOUND. Reservation
  remains one atomic `INSERT…SELECT`; `usage_id is None ⟺ key_source != 'owner'`
  (verified in `resolve_client`); `finalize_usage` UPDATEs the reserved row rather
  than inserting a second, and `record_usage` for user-key runs writes
  `key_source='user'`, which the cap's `key_source='owner'` filter ignores. No
  double-count, no bypass found.
- **(b) Migration additive + idempotent** — SOUND. `_add_column_if_missing` reads
  `PRAGMA table_info` and skips existing columns; `ALTER TABLE ADD COLUMN …
  INTEGER DEFAULT 0` is schema-only (no table rewrite) and safe on a live volume.
  Pre-migration rows read `tokens_counted = 0` = "unknown", never "free".
- **(c) UNCOUNTED vs zero** — HOLDS in `src/tokens.py` (`_whole`/`_build` return
  `counted=False` on anything unusable; bool/negative/string/null all guarded) and
  in the SQL (`session_token_totals` reports `uncounted_calls = calls - counted_calls`).
- **(d) Concurrency around the reserved row** — SOUND. Reservation commits before
  the job is submitted; `finalize_usage`/`release_usage`/`record_usage` are single
  statements under per-thread connections with WAL + 10s busy timeout. No new
  check-then-act race introduced.

## Findings (ranked)

**1 · P36/P5 · web/routes_discover.py:128 · HIGH**
The discover route unpacks the new tuple (`raw, usage = resolved.client.generate(…)`,
line 109) and stashes `job.token_usage = usage` (line 110), but the `finally` block
calls `finalize_usage(ctx.db, usage_id, resolved.provider, resolved.model)` **without
the `usage` argument**, and there is **no `record_usage` branch** for non-owner
(user-key / `none`) discover runs. Result: an owner-key discover call finalizes the
reserved row as `tokens_counted = 0` (unknown) despite the usage being captured in
memory, and a user-key discover call is never written to `usage_events` at all —
so discover spend is silently absent from the meter forever. The summary path got
`_record_spend` + `finalize_usage(usage=…)`; its sibling discover path did not.
*Fix:* pass `job.token_usage` into `finalize_usage`, and add a
`record_usage(..., resolved.key_source, usage)` branch for `usage_id is None`,
mirroring `web/routes_summaries.py:_record_spend`.

**2 · P2 · src/llm_providers.py:281,355 · HIGH**
Hosted `summarize_paper` returns `_coerce_summary(_extract_json(raw)), usage`, but
`_extract_json`/`_coerce_summary` raise `ProviderResponseError` on an unusable
reply **before** the pair is returned — and inside `generate()` itself, DeepSeek's
bad-shape branch and Anthropic's "declined"/"no text block" branches raise before
`from_openai_style`/`from_anthropic` read the `usage` block. In every such case the
model was billed and the usage is present in the response but never read, so
`job.token_usage` stays `UNCOUNTED` and the route records a billed call as "unknown".
The M1.B.3 guarantee ("a failed run records the tokens actually spent") is honoured
only for Ollama's return-`None` path, not for the hosted providers that actually
bill. *Fix:* on parse/coerce failure have hosted `summarize_paper` return
`(None, usage)` (or re-raise carrying the usage), and add a hosted-client
failed-but-billed regression test.

**3 · P22 · web/routes_summaries.py:310 · LOW**
The `except BaseException` handler re-invokes `_record_spend` when `provider_called`
is true, with no flag distinguishing "success-path write already ran" from "failed
before writing". For the owner-key `finalize_usage` path this is idempotent (UPDATE);
for the user-key `record_usage` path a commit that fails after the INSERT could, in
theoretical edge cases, re-insert. SQLite commit atomicity makes a durable double-row
unlikely. *Fix (cheap hardening):* set a `recorded` flag before the success-path
write and skip the handler's write when set.

**4 · P21 · src/user_store.py:212 · LOW**
`session_token_totals` (and `TokenUsage.__add__`) ship with tests but no production
caller — M1.C is a later step. Its `since` parameter must be formatted to match
`usage_events.created_at` (`CURRENT_TIMESTAMP` → "YYYY-MM-DD HH:MM:SS"); an ISO-8601
`T`/`Z` timestamp would mis-compare (`'T' > ' '`) and exclude every row. *Fix:* when
M1.C wires the endpoint, confirm the session-cookie issue time is formatted to that
exact shape before the `>=` comparison.

## Why the suite stayed green

The source-scan guard `test_m1a2_no_caller_discards_usage` asserts only that each
call site *unpacks both halves* of the tuple (`^\s*\w+\s*,\s*\w+\s*=`) — not that the
usage reaches the database. The discover path unpacks correctly (finding 1) and the
hosted providers return the pair on the happy path (finding 2), so the scan passes
while the persistence is incomplete downstream. The scan also hardcodes its
6-file/4-class lists, which will themselves go stale when a new caller or provider
is added.

## Not covered

Per the reviewer charter: general logic/algorithmic correctness, full race analysis
(only a light check), authentication/authorization, injection, performance,
dependency/supply-chain, and test quality beyond the source-scan note above.

## Required to flip to APPROVED

1. Fix finding 1 (discover path persistence) with a regression test asserting the
   reserved row carries `prompt_tokens`/`tokens_counted` and a user-key discover run
   writes a `key_source='user'` row.
2. Fix finding 2 (hosted failed-but-billed usage) with a hosted-client regression
   test.
3. Re-run `venv/bin/python -m pytest tests/ -q` green, then re-sweep the fix commits
   as their own range (they are unreviewed code).
4. Apply findings 3 and 4 or record them in the backlog.

---

# RE-SWEEP — `622ee9f..277fba6`

- **Date:** 2026-09-20 (same session)
- **Range:** `622ee9f..277fba6` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep (cold re-sweep of the fix commit)
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1141 passed, 1 skipped**

RANGE:       622ee9f..277fba6 (caller-supplied; single fix commit)
COMMITS:     1 commit: 277fba6 fix(tokens): gate findings — spend that went unrecorded (M1.A/M1.B)
APPLICABLE:  P5 (sibling calls), P36 (stage output dropped), P22 (stale branch on
             old contract), P19/P19-corollary (source-text assertions), P21 (built-
             not-wired), P2 (silent drop)
CHECKED:     P2, P5, P19, P21, P22, P27, P36 — traced both routes end-to-end,
             read src/tokens.py, src/user_store.py, src/llm_providers.py, src/llm.py,
             src/jobs.py, and the new tests.
NOT COVERED: logic correctness outside the meter, full race analysis, auth, the
             two unrelated file changes bundled in the commit (see note below),
             test quality beyond the source-scan note.

## What each finding fix did — verified

1. **record_spend is now the single path.** grep across production code shows
   `finalize_usage`/`record_usage` called from exactly one non-test place each —
   inside `user_store.record_spend` — and both routes (`routes_summaries.py`,
   `routes_discover.py`) call `record_spend`. The old private `_record_spend` is
   gone. `release_usage`/`reserve_owner_usage` remain as separate concerns
   (give-back / admission) and are used correctly. No stray caller found.

2. **Exception-carried usage is correct on the summary path.** `LLMError.usage`
   defaults to `UNCOUNTED`; DeepSeek/Anthropic `generate()` attach the real
   `from_openai_style`/`from_anthropic` value on their bad-shape / refusal /
   no-text-block branches; `_parsed_or_billed` re-attaches on parse/coerce
   failure. The route's `if not spent.counted: spent = getattr(exc, "usage", None) or spent`
   is mutually exclusive with `job.token_usage` (that assignment only runs after
   `summarize_paper` returns), so it can neither double-count nor misattribute —
   one write, counted from the right source.

3. **The `recorded` flag covers every summary path.** success→record+flag; fail-
   before-model→release; fail-after-model-before-record→record from exception;
   fail-after-record→flag skips the second write. The only residual is the
   pre-existing theoretical case where `record_spend` itself raises mid-write,
   which the flag does not and cannot guard; SQLite commit atomicity still makes
   a durable double row unlikely (unchanged from the prior gate's note).

4. **`session_token_totals` now takes a datetime** and formats via
   `_sqlite_timestamp` (tz-aware→UTC-naive, `%Y-%m-%d %H:%M:%S`), matching
   `CURRENT_TIMESTAMP`. Correct; the test asserts the separator shape directly.
   Only mild nit: a caller passing a *naive local* datetime is treated as UTC —
   the docstring says "in UTC" but only converts tz-aware inputs. M1.C has no
   caller yet (P21), so this is moot today.

5. **USAGE_KIND did not break the cap.** `_resolve_for` reserved under the
   hardcoded `"summary"` before this change, so discover already drew on the
   summary allowance; `USAGE_KIND` only names that. Owner discover still goes
   reserve→`finalize_usage` (UPDATE, one row) and user-key discover inserts
   `key_source='user'`, which the cap's `key_source='owner'` filter ignores. No
   double-count, no bypass. The "discover logged as summary" label is the
   deliberate deferral recorded in TODO.md — correct to defer, since splitting
   kinds also removes discover from the cap unless the cap query changes in the
   same move.

## Finding

**1 · P5/P36 · web/routes_discover.py:131 · MEDIUM**
The finding-2 fix (exception-carried usage) was applied to `routes_summaries.py`
only. Discover calls `resolved.client.generate(...)` **directly** (line 109), not
`summarize_paper`, so it never passes through `_parsed_or_billed`; and its
settlement lives in a `finally` block that reads **`job.token_usage` only** —
there is no `except`, so no `exc` is in scope to recover `exc.usage`. When
`generate()` itself raises a usage-carrying error (DeepSeek bad-shape branch,
Anthropic `stop_reason == "refusal"`, Anthropic "no text block"), the response
reported what the call cost and the model was billed, but `job.token_usage` was
never assigned (it is still the `UNCOUNTED` default from `Job`), so
`record_spend` writes `tokens_counted = 0` and the spend is invisible in the
meter. The common discover failure (a reply `parse_terms` rejects) is covered —
`job.token_usage` is set before `parse_terms` — but the generate-raises-before-
return cases are the exact same "billed but recorded as unknown" hole finding 2
closed for summaries. No test covers it: the three new discover tests exercise
success (usage supplied) and pre-model failure, never model-called-then-raised.
*Fix:* mirror `routes_summaries` — settle in an `except` (or stash the raised
exception) so the discover path prefers `exc.usage` when `job.token_usage` is
uncounted, and add a regression test asserting a refusal/bad-shape discover run
records the exception's `prompt_tokens`.

Confidence: high that the gap exists (traced the code); medium that it fires in
practice (refusal/bad-shape on a keyword-discovery prompt is rare, but it is the
precise M1.B.3 class this gate exists to enforce).

## Notes (not findings)

- **Unrelated changes bundled in the commit.** `Maternal nueroticism.pdf` is
  deleted and `filters.json` is rewritten (term groups collapsed, source
  selection switched to `all: true`). Neither belongs to the token-capture gate.
  They look like working-tree changes swept in by `git add -A`. Not a failure-
  pattern defect, but a hygiene problem: a fix commit should not carry a data-
  file edit and a binary deletion reviewers must then triage. Worth splitting
  out before this is merged/pushed.
- **Source-text assertion.** `test_gate2_both_spending_routes_use_the_shared_recorder`
  asserts `"user_store.record_spend(" in source` and `"def _record_spend" not in
  source` (P19-corollary, same family the prior gate flagged in the m1a2 scan).
  It is supplementary — the two behavioural discover tests would still go red if
  the route reverted — so it is acceptable, but it will silently stop guarding
  if a future rename leaves a stale needle, and it does not prove the *spend*
  reaches the DB (the behavioural tests do that).

## Verdict: REJECTED

The four original findings are correctly and fully fixed, the single-path
`record_spend` refactor is sound, the cap accounting is intact, and the suite is
green (1141 passed). But the finding-2 fix — the one this gate exists to enforce,
"a failed run records the tokens actually spent" — was applied to the summary
route and left off the discover route's `generate()`-raise path, where billed
usage still lands as `tokens_counted = 0`. One MEDIUM finding; the same P5
sibling-call class that caused the first rejection.

Required to flip to APPROVED:
1. Recover `exc.usage` in the discover settlement (or otherwise record the
   exception-carried usage when `generate()` raises), with a regression test
   asserting a refusal/bad-shape discover run records the exception's tokens.
2. Split the unrelated `Maternal nueroticism.pdf` deletion and `filters.json`
   rewrite out of the fix commit (or record why they belong here).
3. Re-run `venv/bin/python -m pytest tests/ -q` green, then re-sweep.
