# Learning-QA gate — chunk 3 (2026-09-15)

**Verdict: APPROVED**

Binary: APPROVED — no API-key leak into log/response/exception/traceback, no
job left "running", no live cross-user read, no silent summary-parse failure,
no un-failable test; `pytest` exit 0. Two medium + three low findings carried
(all latent — this chunk is backend infrastructure and has no consumer yet).

---

RANGE:       origin/main..HEAD (`git diff origin/main..HEAD > /tmp/sweep.diff`).
             NOTE: the caller cited commit `dda6411`; the actual single commit in
             this range is `fb4e556` (the caller's description matches it).
COMMITS:     1 — fb4e556 feat(llm): pluggable providers, credential resolver,
             and a background job runner
             (adds src/llm_config.py, src/llm_providers.py, src/crypto.py,
             src/jobs.py, llm_config.yaml, requirements-web.txt, tests/web/)
APPLICABLE:  P2, P14, P15, P19, P21, P22, P25, P27, P29, P32, P35;
             S1, S3, E2, E5, E7, L1, L4, L8, L9
CHECKED:     same list
NOT COVERED: logic correctness beyond the failure patterns; the web routes /
             consumer (a later phase, so resolver/job/encryption are not
             exercised end-to-end); live provider calls (mocked — L9
             integration-only); performance under load; the Anthropic/DeepSeek
             SDK's internal redaction behaviour (assumed, not probed).

Test suite:  /opt/homebrew/bin/pytest tests/ -q  →  exit code 0 (285 passed,
             9 skipped). Success judged by exit code, not output scraping. The
             9 skips are the pre-existing GUI tests (no PyQt6 in this 3.11
             interpreter), unrelated to this chunk.

---

## FINDINGS (ranked)

### 1. MEDIUM · `expired` status promised by the docstring is not implemented (P35/P22 family)

- File: `src/jobs.py:11-14` (module docstring) vs `JobStatus` (`src/jobs.py:42-48`)
  and `get()` / `_expire_old()` (`src/jobs.py:170-182`, `201-213`).
- Risk: the docstring says "callers are told so by an explicit `expired` status
  rather than by a poll that never returns." No `EXPIRED` status exists.
  `_expire_old()` deletes finished jobs, so `get()` returns `None` for an
  expired job — byte-identical to "unknown id" and "another user's job". A
  poller cannot distinguish "your job was dropped (TTL/redeploy)" from "bad id",
  so a client may surface "job not found" instead of "expired — re-submit". No
  data loss, leak or hang — but a contract the doc explicitly claims to solve is
  not solved.
- Fix: add `EXPIRED` to `JobStatus` and have `_expire_old()` retain a tombstone
  through the TTL window so `get()` can return it — or correct the docstring and
  make `get()` return a typed "not-found vs expired vs foreign" distinction the
  poller can branch on.
- Confidence: high

### 2. MEDIUM · ownership check is opt-in via `owner=None` default (S3/P25 family)

- File: `src/jobs.py:170-182` (`get`), `184-193` (`cancel`). The default
  `owner=None` skips the owner comparison.
- Risk: the docstring claims "Ownership is checked here rather than at each call
  site so a job id guessed or copied between users does not leak another user's
  results." But the check is a *defaulted* parameter: any call site that omits
  `owner` (the easy thing) gets unrestricted read of any job's `result` and can
  cancel it. No call site exists in this chunk (routes are a later phase), so
  this is latent, not live — but it is the exact class the docstring claims to
  prevent, and `test_another_user_cannot_read_or_cancel_a_job` only exercises
  the owner-passed path.
- Fix: make `owner` a required positional argument (no default) so the check
  cannot be silently skipped, or key the registry by owner. Add a test asserting
  `get()`/`cancel()` without an owner are refused.
- Confidence: high

### 3. LOW · two client families have incompatible failure contracts (P22, forward-looking)

- File: `src/llm_providers.py:250-254, 321-325` (hosted clients raise typed
  `LLMError`s) vs `src/llm.py:77-149` (Ollama returns `None`). `resolve_client()`
  can return either.
- Risk: a uniform caller of `ResolvedLLM.client.summarize_paper(...)` must handle
  both `None` and exceptions. The divergence is deliberate and documented in the
  module docstring, but the reconciliation is deferred to the not-yet-written web
  route. The existing CLI consumer's broad `except Exception` collapses the typed
  distinction (`NoLLMCredentialError` vs `ProviderUnavailableError` vs
  `ProviderResponseError` all become a logged `False`), so "no key" and "provider
  down" reach the user identically today.
- Fix: at the resolver boundary, normalize both families to one result type (or
  one exception hierarchy) so the route can branch on "no key / provider down /
  bad output" without inspecting which client family it got.
- Confidence: medium

### 4. LOW · vacuous "nothing was written" assertion (P27-adjacent)

- File: `tests/web/test_crypto.py:59` — `assert monkeypatch.delenv(crypto.ENV_VAR,
  raising=False) is None`.
- Risk: `monkeypatch.delenv()` always returns `None`, so this asserts a constant
  and proves nothing, while reading as if it verified "no file was written". The
  test still can fail via its other asserts (`is_enabled() is False`,
  `pytest.raises`), so it is not a dead test — but the line is misleading dead
  weight.
- Fix: drop it, or assert something that actually observes "nothing was written"
  (e.g. assert no key file was created).
- Confidence: high

### 5. LOW · `mask()`/`last4()` built but not yet wired (P21, expected for this phase)

- File: `src/crypto.py:118-134`; no caller in `src/` (grep-verified — only the
  tests import them).
- Risk: `mask()`/`last4()` are the intended gate against a key reaching a
  response/template/log, and are unit-tested, but nothing in this chunk calls
  them — the web routes are a later phase. The protection is real only once the
  routes/templates use them; today's coverage proves the helpers work, not that
  they are used.
- Fix: when the routes phase lands, route every key-adjacent value through
  `mask()`/`last4()` and add a boundary test asserting the serialized response
  contains only the masked form (P25 — test the boundary, not just the helper).
- Confidence: high

---

## Specific questions from the caller

**1. Can an API key leak into a log, a response, an exception message or a
traceback anywhere in this diff?** No. Every error message names the env var
(`ANTHROPIC_API_KEY`, `{pconf.api_key_env}`) or the provider, never the value;
the key travels only in the `Authorization` header / SDK client. `job.error` and
the traceback carry worker exceptions, none of which embed a key (grep-verified
over the diff). No real secret is committed: the `sk-ant-…` literals in the tests
are fake (`sk-ant-…1234`, `sk-ant-SUPERSECRETVALUE`, `sk-ds-ALSOSECRET`). The
response-side gate (`mask`/`last4`) exists and is tested but has no caller yet
(finding 5).

**2. Can the job registry lose a job, leave one "running", or let one user read
another's?** Lose: only by TTL expiry/redeploy (documented in-process
limitation); `_expire_old` runs only on `submit()`, so finished jobs linger until
the next submit (minor memory growth, not loss); queued/running jobs are never
expired (tested). Leave "running": no — the P15 guard wraps the whole body
including setup, and a `finally` block settles the status for both `Exception`
and `BaseException` (`KeyboardInterrupt`/`SystemExit`); tested
(`test_a_baseexception_does_not_leave_the_job_running`,
`test_setup_failure_before_running_is_also_guarded`). Cross-user: the check
exists and works when `owner` is passed (tested), but the `owner=None` default
bypasses it (finding 2) — latent, no live caller in this chunk.

**3. Can the summary-parsing contract between provider and consumer fail
silently?** No. Unparseable/empty/all-empty replies raise `ProviderResponseError`
instead of returning blank strings — the exact P19/P14 failure the existing
`src/llm.py` prose-parser has. The canonical shape `{key_findings: [str],
methodology: str, conclusions: str}` matches both `OllamaClient.summarize_paper`
and `db.insert_summary` (`key_findings` stored as a JSON list, confirmed at
`src/db.py:450`, `563-564`). Adversarial tests cover the Qwen-prose shape, empty
string, all-empty JSON, and a JSON array. Residual (non-silent, deliberate):
partial summaries (findings present, methodology/conclusions empty) are accepted,
and the two client families have a `None`-vs-raise failure-mode divergence
(finding 3).

**4. Can any test in `tests/web/` not fail?** No test is un-failable in the P27
sense (none is assert-less, returns a bool, or asserts a self-computed constant).
Two weak-but-adjacent assertions exist — `test_crypto.py:59` (vacuous
`delenv is None`, finding 4) and `test_crypto.py:108`
(`KEY[:-4] not in mask(KEY)`, true by length alone) — but each sits beside a real
assertion, so its test still fails on a mutation of the named line. The HTTP/SDK
mock tests assert real quantities (auth header value, retry count, error type,
exact field values).

---

## Test-quality notes (informational)

- `test_key_never_appears_in_logs` exercises the DeepSeek 503 path for leakage
  but not the Anthropic SDK error paths (where an SDK message could in theory
  embed a key). It guards what it exercises; the stronger gate is `mask()`/`last4()`
  once wired (finding 5).
- `test_default_anthropic_model_is_a_current_id` pins the model against a
  hand-written `current_ids` set. Verified against the live Claude model list
  (2026-09-15): the configured default `claude-sonnet-5` is current, and
  `claude-sonnet-4` (from the original spec) is correctly rejected as
  non-existent. `~/.claude/standards/llm-integration.md` L1 is stale (still lists
  `claude-sonnet-4-6` as default) — already flagged in `TODO.md`, and it lives in
  a separate repo.
- `test_each_pinned_package_is_real_and_importable` uses `importorskip`, so under
  the 3.11 interpreter packages absent there are skipped, degrading "real and
  importable" to "present in the file" — the documented two-interpreter situation
  in `TODO.md`.

---

## Verdict

APPROVED. Against the four questions the caller flagged, the chunk is clean:
no key leak anywhere in the diff, no job left on "running" (P15 guard complete
and tested including `BaseException`), no silent summary-parse failure (unusable
replies raise; the shape matches the existing Ollama client and the DB), and no
test that cannot fail. The suite exits 0. Findings 1 and 2 are medium but latent
— there is no consumer of `JobRegistry` in this phase, so neither is a live bug
against a current artifact. Finding 2 (the opt-in ownership check) is the
highest-priority carried item and must be resolved before the web-routes phase
lands, since that is the moment a caller could omit `owner`. Findings 3–5 are
low/forward-looking.

Next step: carry findings 1–5 to `TODO.md` (as the chunk-1/chunk-2 gates did),
then resolve finding 2 before or within the routes phase. No re-sweep is required
for this gate — no fix commits were produced.
