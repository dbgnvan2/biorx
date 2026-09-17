# Learning-QA gate — chunk 2 (2026-09-15)

**Verdict: REJECTED**

Binary: REJECTED — 1 medium finding (connection leak) in the area the caller
flagged, plus 3 low findings.

---

RANGE:       origin/main..HEAD (`git diff origin/main..HEAD > /tmp/sweep.diff`,
             caller-supplied range; two commits)
COMMITS:     2 — 602e21e refactor(gui): extract paper-metadata and filter-store
             helpers into src/paper_meta.py and src/filters_store.py
             3abd9c2 feat(db): per-thread connections, WAL, env-driven paths,
             web-app tables
APPLICABLE:  P2, P4, P5, P8, P12, P19, P21, P24, P28, P30
CHECKED:     P1, P2, P4, P5, P6, P8, P12, P19, P21, P24, P28, P30
NOT COVERED: caller-excluded none. Out-of-scope families per the reviewer's
             mandate: logic correctness, security (the `users.llm_key_ciphertext`
             encryption scheme), performance under load, dependency/supply chain.
             The concurrency test's flakiness under heavy contention was not
             stress-measured, only run once (green).

Test suite:  /opt/homebrew/bin/pytest tests/ -q  →  exit code 0 (183 passed,
             4 skipped). Success judged by exit code, not output scraping.

---

## FINDINGS (ranked)

### 1. MEDIUM · Per-thread connections leak until process exit (P30 family)

- File: `src/db.py:65-81, 615-624` (and the `conn` property, `src/db.py:83-94`)
- Risk: every thread that touches `db.conn` creates a connection that is
  appended to `_all_conns` and is never released except by `close()`. The GUI
  spawns a **fresh `QThread` per operation** — SearchWorker (`gui.py:898`),
  download workers (`gui.py:1586`, `gui.py:1980`), SummarizationWorker and
  AbstractFetchWorker — and each worker calls `insert_paper`/`get_*`, minting a
  new connection. `close()` is called **only** in `MainWindow.closeEvent`
  (`gui.py:2051-2053`), i.e. at app shutdown. The `threading.local()` handle is
  freed when the OS thread dies, but the connection object is also held by the
  strong reference in `_all_conns`, so it — and its file descriptor and page
  cache — is never garbage-collected.
- Why it hides: the old code had one shared connection, so there was nothing to
  leak; the leak produces no error, just a slow growth of fds/memory over a
  long GUI session (and per background job in the planned web app).
- Fix: release a thread's connection when that thread finishes — e.g. a
  `weakref.finalize` on the thread-local, or have each worker call a
  `db.release()` in a `finally`, or (correct for the web app) a bounded pool
  with checkout/return. Add a test that asserts the live connection count is
  bounded after N short-lived worker threads.
- Confidence: high

### 2. LOW · OpenAlex User-Agent changed during a "verbatim" extraction

- File: `src/paper_meta.py:546` vs removed `gui.py` line (old value
  `mailto:research@example.com`)
- Risk: the module docstring states "Extracted verbatim from gui.py …
  Behaviour is unchanged". The OpenAlex `User-Agent` contact email changed from
  `research@example.com` to `[owner-email]`, so the claim is false, and a
  real personal email is now hardcoded in source (P4 — a literal that should be
  config).
- Fix: either restore the placeholder, or correct the docstring to name the
  change, and read the contact from an env var like the module's other
  constants (`OPENALEX_USER_AGENT` from `BIORX_OPENALEX_CONTACT`).
- Confidence: high

### 3. LOW · `BUSY_TIMEOUT_MS` frozen at import, unlike `default_db_path()`

- File: `src/db.py:27`
- Risk: the busy timeout is read once at module import; setting
  `BIORX_DB_BUSY_TIMEOUT_MS` after `import src.db` has no effect. This is
  inconsistent with `default_db_path()` (a function, reads env at call time)
  and is a frozen-module-constant smell (a future test/config that redirects
  the env var after import will silently no-op).
- Fix: read it lazily (function or property), or document the import-time
  semantics explicitly.
- Confidence: medium

### 4. LOW · `summaries.created_by_user_id` has no writer (P21, forward-looking)

- File: `src/db.py:257` (migration) vs `insert_summary` (`src/db.py:339-384`)
- Risk: the column is added but `insert_summary` neither accepts nor populates
  it, and `INSERT OR REPLACE` would NULL it on re-summarize even if a writer
  existed. Acknowledged as forward-looking for the web app (which is a later
  phase), so this is not a bug today — but it is "built, not yet wired", and
  the schema will silently hold NULL provenance forever unless the eventual
  writer is made to populate it and a test asserts the field is written.
- Fix: when the web-app summary path lands, add the `created_by_user_id` to the
  `INSERT OR REPLACE` value list and assert it round-trips.
- Confidence: medium

---

## Specific questions from the caller

- **P12 — did the gui.py extraction drop any behaviour/side effect?**
  No. Every extracted function (`load_filters`, `save_filters`,
  `filter_is_enabled`, `_filter_has_text`, `_pdf_url`, `_paper_link`,
  `_scrape_abstract_from_url`, `_fetch_openalex_abstract`) is re-imported under
  its original name and every call site in gui.py resolves (grep-verified at
  lines 94-97, 217-220, 291, 303, 339, 454, 554, 811, 833, 884, 1245, 1497,
  1503-1569, 1933). The only deviation from "unchanged" is the OpenAlex
  User-Agent value (finding 2). The dead `_TextExtractor` class was carried
  over intact — preserved, not dropped.
- **Deadlock?** None found. `_conns_lock` is held only during the list append
  (`src/db.py:79-80`) and during the snapshot in `close()`; no lock is held
  across queries or commits. WAL + `busy_timeout` serialize concurrent writers.
  The 4-thread×20-write concurrency test passes (exit 0).
- **Leak?** Yes — finding 1.
- **P8 — is the migration safe on a populated DB?** Yes. `CREATE TABLE IF NOT
  EXISTS`, `PRAGMA journal_mode=WAL` (idempotent, degrades to a warning on
  error), and `_add_column_if_missing` (reads `PRAGMA table_info` instead of
  the old bare `except: pass` — which also fixes a latent P2 where a real
  ALTER failure was indistinguishable from "column exists"). One test gap: the
  dirty-state test populates the DB with the *new* code, so it never exercises
  a genuinely old-schema DB against the new `created_by_user_id` migration —
  a coverage note, not a code defect.

---

## Test-quality notes (informational)

- `tests/test_filters_store.py:test_load_reads_the_real_filters_json` reads the
  developer's live `filters.json` as its fixture. Read-only and it fails
  loudly if the file is absent/malformed, so it is not P28-destructive — but it
  couples the suite to a specific machine's local data.
- `tests/test_paper_meta.py` mocks all HTTP; the `gui.py`-import tests set
  `QT_QPA_PLATFORM=offscreen` before import and `gui.py`'s `QApplication` is
  under `__main__` (`gui.py:2063`), so imports are side-effect-free. No
  module-level `Database()` instantiation exists.

---

## Verdict

REJECTED. Finding 1 (medium, connection leak) is a genuine resource leak
introduced by the threading refactor, in exactly the area the caller asked to
scrutinize, and it affects the primary current artifact (the desktop GUI) over
long sessions. Findings 2-4 are low and do not independently block, but should
be addressed alongside. The extraction (P12), deadlock question, and migration
safety (P8) are otherwise clean.

Next step after fixing finding 1: re-sweep the fix commit as its own range
(`<pre-sweep-tip>..HEAD`) — fixes are unreviewed code.

---

# Fix-loop 1 — re-sweep of commit 0696c33 (2026-09-15)

**Verdict: REJECTED**

Binary: REJECTED — finding 1 (medium, the connection leak) is not actually fixed
for the primary artifact (the desktop GUI). Findings 2–4 of the prior gate are
confirmed fixed.

---

RANGE:       origin/main..HEAD (`git diff origin/main..HEAD > /tmp/sweep.diff`);
             three commits. Newly-reviewed surface is the fix commit
             `3abd9c2..0696c33`; 602e21e and 3abd9c2 were covered by the prior
             gate above.
COMMITS:     0696c33 fix(db): release per-thread connections; address chunk-2
             gate findings
APPLICABLE:  P2, P4, P5, P19, P21, P24, P27, P28, P30, P31, P32
CHECKED:     P2, P4, P5, P19, P21, P24, P27, P28, P30, P31, P32
NOT COVERED: same families as the prior pass (logic correctness, security,
             performance, supply chain). The GUI worker lifecycle was inspected
             for the reaper question and the QThread semantics were probed
             empirically, but no full GUI was launched.

Test suite:  /opt/homebrew/bin/pytest tests/ -q  →  exit code 0 (192 passed,
             4 skipped). Success judged by exit code, not output scraping.

---

## FINDINGS (fix-loop 1, ranked)

### 1. MEDIUM · The reaper cannot see QThread workers — the leak is not fixed for the GUI (P30)

- File: `src/db.py:116-143` (`_reap_dead_threads`), `src/db.py:84` (registry),
  `src/db.py:165-181` (`conn`); and the absence of any `release()` call in
  `gui.py`.
- The reaper decides a thread is dead by `thread.is_alive()`, on the object the
  registry stored via `threading.current_thread()`. The GUI's workers run on a
  `QThread` (`moveToThread(thread); thread.started.connect(worker.run)` —
  `gui.py:491-492, 512-513, 899-900, 1587-1588, 1981-1982`). Python executing on
  a Qt thread is not a `threading.Thread`; `threading.current_thread()` there
  returns a `_DummyThread`, and a `_DummyThread.is_alive()` returns **True
  forever** — verified empirically on this runtime, both inside `run()` and after
  `QThread.wait()`, and for a native thread for 3+ seconds and after `gc`.
- Consequence: no GUI worker's connection is ever reaped. Every search /
  download / summarize / abstract-fetch mints a connection registered under a
  `_DummyThread` that never reports dead, held in `_conns` until `close()` at
  `MainWindow.closeEvent` (`gui.py:2051-2052`). That is byte-for-byte the
  finding-1 behaviour the fix was written to stop.
- Why the tests stay green: `tests/test_db_concurrency.py:116-135, 138-152` use
  `threading.Thread`, whose `is_alive()` correctly flips to False after `join()`.
  The fix was validated against the test's thread population, not the production
  one (the P30-corollary / P32 shape the prior gate named as a risk). The new
  leak test correctly asserts *closed*, but against the wrong thread type.
- Fix: the liveness signal is the wrong tool for native threads. Wire
  `db.release()` into every GUI worker's `finished` handler (the method exists
  but has zero production call sites — grep shows only the test calls it), or use
  a native-thread-safe liveness mechanism (a per-thread sentinel the worker owns,
  with `weakref.finalize`), or both. Add a test that drives a real QThread
  worker (or a native thread) and asserts the connection closes — the current
  tests cannot fail against the real defect.
- Confidence: high

### 2. LOW · check_same_thread=False removes the cross-thread safety net globally

- File: `src/db.py:101-105`
- The flag is genuinely required for the reaper to close a dead thread's handle
  from another thread (verified: with the default True, even `conn.close()` from
  another thread raises `ProgrammingError` on this Python 3.12). But it applies
  to every connection and every operation, so the loud "created in a thread,
  used in another" error is now suppressed everywhere: a future regression that
  breaks thread-locality in the `conn` property would use connections cross-thread
  silently instead of raising. Thread-locality is currently correct, so this is
  latent, not live.
- Fix: document the intentional removal, or scope the unsafe access (keep normal
  connections `check_same_thread=True`; give the reaper a narrow close path).
- Confidence: medium

### 3. LOW · close() can close a live worker's connection mid-query

- File: `src/db.py:705-715`, `gui.py:2051-2052`
- At shutdown, `close()` closes every handed-out connection from the main thread;
  with the thread check disabled there is no longer a `ProgrammingError` to flag
  that a still-running worker's connection was closed under it. Pre-existing
  shutdown race; note only.

### 4. LOW · double-close is possible but benign

- File: `src/db.py:145-163, 705-715`
- `release()` pops a key under `_conns_lock` and `close()` snapshots under the
  same lock; a race can hand both the same connection object, so both call
  `conn.close()`. Verified `sqlite3.Connection.close()` is idempotent (second
  close is a no-op), so this is harmless. `release()`-twice and `close()`-twice
  are each individually guarded.

### 5. INFO · the "no personal email" guard is narrower than its intent

- File: `tests/test_paper_meta.py:177-179`
- `assert "@me.com" not in source` is a legitimate absence-over-source guard
  (P19-corollary use), but it checks one domain suffix and would miss a different
  personal address (e.g. another personal address). The underlying fix is
  correct; the guard is just narrower than "no personal address".

---

## Prior findings 2–4: confirmed fixed

- **OpenAlex contact** — `src/paper_meta.py:34-40` reads `BIORX_CONTACT_EMAIL`
  with default `research@example.com` restored; `openalex_user_agent()` builds it
  at call time. Test added. FIXED.
- **BUSY_TIMEOUT_MS** — now `busy_timeout_ms()` (`src/db.py:29-38`), read at
  call time; env-after-import test added. FIXED.
- **insert_summary provenance** — `created_by_user_id` now a parameter and in the
  `INSERT OR REPLACE` value list (`src/db.py:426-474`); round-trip test added,
  plus a no-provenance test confirming the desktop path still works. FIXED.

---

## Specific questions from the caller

- **Is the reaper defeatable?** Yes — and it is defeated in production, not just
  in theory. The GUI's QThread workers are `_DummyThread`s whose `is_alive()` is
  True forever, so the reaper collects nothing for the primary artifact
  (verified with a real PyQt6 QThread probe, not just by reasoning).
- **Does check_same_thread=False introduce a cross-thread hazard?** Not in normal
  operation — thread-locality is intact and the only cross-thread access is the
  deliberate reaper/close path. It does remove the loud error that would catch a
  future break of that locality (finding 2).
- **Can close()/release() double-close?** Yes, via a release()/close() race, but
  `sqlite3` close() is idempotent so it is benign (finding 4).

---

## Verdict

REJECTED. Finding 1 — the medium the whole loop exists to close — is not fixed
for the desktop GUI, the primary current artifact. The reaper's
`threading.Thread.is_alive()` test is defeated by QThread's `_DummyThread`
semantics, and `release()` (the intended immediate hand-back) is built but not
wired into any GUI worker. The test suite's leak tests pass only because they use
`threading.Thread`, which does not model the production workload. Findings 2–4
of the prior gate are genuinely fixed.

Next step: fix finding 1 (wire `release()` into the GUI worker `finished`
handlers and/or make liveness detection native-thread-safe), add a QThread-driven
regression test, then re-sweep that fix commit as its own range.

---

# Fix-loop 2 — re-sweep of commit 82d524a (2026-09-15)

**Verdict: APPROVED**

Binary: APPROVED — the connection leak is fixed for the primary artifact (the
desktop GUI), verified empirically against a real QThread, not by taking the
test's word. No blocking findings.

---

RANGE:       origin/main..HEAD (`git diff origin/main..HEAD > /tmp/sweep.diff`);
             four commits. Newly-reviewed surface is the fix commit
             `0696c33..82d524a`; 602e21e, 3abd9c2 and 0696c33 were covered by the
             two prior gates above.
COMMITS:     82d524a fix(db): release connections by holder lifetime, not thread
             liveness
APPLICABLE:  P2, P4, P5, P19, P21, P24, P25, P27, P28, P30, P34
CHECKED:     P2, P4, P5, P19, P21, P24, P25, P27, P28, P30, P34
NOT COVERED: same families as prior passes (logic correctness, security,
             performance under load, supply chain). No full GUI was launched;
             the QThread lifetime was probed with QCoreApplication, not a
             QApplication/event-loop GUI.

Test suites (judged by exit code, not output scraping):
- `/opt/homebrew/bin/pytest tests/ -q`        → exit 0 (192 passed, 7 skipped).
  No PyQt6 in this interpreter; the QThread test (test_db_concurrency.py:149)
  and the two GUI wiring tests (311, 330) skip, as expected.
- `QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q` → exit 0 (201 passed).
  PyQt6 present; the QThread regression test and both GUI wiring tests run and
  pass.

---

## The fix, restated

The prior fix's reaper keyed liveness on `threading.Thread.is_alive()`, which
stays True forever for the `_DummyThread` a QThread worker yields — so it
collected nothing in the GUI. `release()` existed but had no production caller.
This fix removes liveness detection entirely. Each connection is now owned by a
`_ConnectionHolder` stored in `threading.local`; a module-level
`_release_connection` is registered with `weakref.finalize(holder, …)` and runs
when the holder dies — which happens when the owning thread (of any type) exits,
or when `release()` drops it. `SearchWorker.run()` now calls `db.release()` in a
`finally`.

---

## Caller questions, answered with probes (not the test suite)

A throwaway probe (`/tmp/probe_fix2.py`, run under the PyQt6 interpreter) drove a
real `QThread` and inspected the connection state directly.

**(a) Is the connection really closed after a real QThread finishes?** Yes.
`Worker.run()` minted a connection on a `QThread`; `threading.current_thread()`
there was `_DummyThread` (owner ident 6090…, main ident 8666… — different
threads). After `qthread.quit(); qthread.wait()`, `conn.execute("SELECT 1")`
raised `ProgrammingError: Cannot operate on a closed database` **before any
`gc.collect()`**, and the connection was already unregistered from `_conns`. The
finalizer fires synchronously on the dying thread — it does not wait for a GC
pass. The old reaper, by contrast, would have left this same connection open
(that was the fix-loop-1 rejection).

**(b) Does `weakref.finalize` keep the Database alive, or create a cycle?** No.
The finalizer holds a weak ref to the holder and strong refs to its args
(`conn`, the shared `_conns` dict, `_conns_lock`) — none of which point back to
the holder or the Database. Probed: with a worker thread still alive and holding
a connection, the Database was collected (`weakref.ref(db)() is None`) and the
connection was still closed correctly when that thread later died;
`gc.garbage` stayed empty (no uncollectable cycles). A main-thread holder
survives `close()` the same way. The `_conns` dict's lifetime is extended to the
longest-lived holder — which is correct, since finalizers pop from it — and is
released once the last finalizer fires.

**(c) Is a finalizer closing a sqlite connection on a GC thread safe here?**
Yes, on three grounds. (1) CPython has no separate GC thread, and the holder is
never part of a cycle, so the finalizer fires on the owning thread at refcount
drop (thread death or `release()`), not on some other thread — `check_same_thread
=False` is not even exercised on the common path. (2) Even if a holder did reach
the cycle collector, `check_same_thread=False` makes a cross-thread `close()`
legal, and single-owner connections guarantee nothing else is using it at that
moment. (3) `_release_connection` pops the key under `_conns_lock` but calls
`conn.close()` outside the lock, and wraps it in `try/except sqlite3.Error`, so
no lock is held across the close. One benign edge (note 3 below): at interpreter
shutdown the `atexit` finalizer path could in theory surface a non-`sqlite3.Error`
exception, but that only prints "Exception ignored" and the OS reclaims the fd.

**(d) Can any test in `tests/test_db_concurrency.py` not fail?** No. Every test
asserts a real quantity, not a tautology: `_is_closed()` performs an actual
`execute("SELECT 1")` and asserts `ProgrammingError` (not a registry-size floor);
`handed_out` holds strong refs so a broken finalizer would leave connections open
and fail the assert; the QThread test asserts both the `_DummyThread` type (so a
change in PyQt's thread shape fails loudly) and a closed connection **before**
`database.close()`, so it cannot pass via the teardown path. Critically, the new
QThread test *would fail* against the old reaper — it is the missing regression
the prior gate demanded. The weakest assertion is
`test_release_is_safe_when_this_thread_never_connected` (asserts "did not raise",
not "closed something"), but it guards a real boundary — `SearchWorker.run()`'s
`finally` calls `release()` even when `save_to_db=False` and no connection was
ever taken.

---

## FINDINGS (fix-loop 2, ranked)

### 1. LOW · `release()` is wired only into SearchWorker, not the other workers

- File: `gui.py:189` only. `SummarizationWorker`, `AbstractFetchWorker`,
  `PdfSectionWorker`, and `BatchPdfDownloadWorker` have no explicit `release()`.
- Why this is not the old leak: each worker still gets a fresh `QThread`
  (`gui.py:495, 517, 904, 1592, 1986`), and the holder-lifetime finalizer closes
  those connections when the QThread dies — verified in (a). `release()` is now a
  *latency* optimization (immediate hand-back for pooled threads), not the sole
  release path. The old "no caller" defect is nonetheless fixed: SearchWorker is
  the one caller that both churns hardest and takes the connection under the
  widest surface.
- Fix (optional): give the other db-touching workers the same `finally`, if/when
  a thread is ever pooled. Not required for correctness today.
- Confidence: high that it is not a leak; the "no caller" gap is closed.

### 2. LOW · `check_same_thread=False` still suppresses the cross-thread net globally

- Carried forward from fix-loop-1 finding 2, now even less load-bearing (the
  finalizer fires on the owning thread), but still latent: a future break of
  thread-locality in the `conn` property would use connections cross-thread
  silently instead of raising. Note, not a blocker.
- Confidence: medium.

### 3. LOW · `_release_connection` catches only `sqlite3.Error`

- File: `src/db.py:52`. If a connection's `close()` raises a non-`sqlite3.Error`
  during the `atexit` finalizer sweep at interpreter shutdown, it prints
  "Exception ignored" rather than being swallowed. Benign (OS reclaims fds);
  broadening the `except` to `Exception` would silence it.
- Confidence: low impact.

---

## Prior findings, status

- **fix-loop-1 finding 1 (medium, reaper blind to QThread)** — FIXED. Liveness
  detection removed; holder-lifetime finalizer verified against a real QThread
  in (a).
- **fix-loop-1 finding 2 (check_same_thread=False)** — still present, low (note
  2 above).
- **fix-loop-1 findings 3, 4 (shutdown close race, benign double-close)** —
  unchanged, pre-existing, non-blocking.

---

## Verdict

APPROVED. The medium the whole loop exists to close — the GUI connection leak —
is fixed for the primary artifact, and this time the fix is validated against the
real production thread type (a QThread's `_DummyThread`), not a
`threading.Thread` stand-in. The finalizer keyed on holder lifetime closes the
connection synchronously on thread death (probed, no `gc.collect()` needed), does
not keep the Database alive or form a cycle, is safe to close sqlite from, and
the new tests genuinely fail if the mechanism regresses. Both test suites exit 0.
Remaining findings are low and do not block.
