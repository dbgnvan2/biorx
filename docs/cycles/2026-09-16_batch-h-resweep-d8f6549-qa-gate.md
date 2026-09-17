# Learning-QA gate — batch-h (re-sweep of fix commits 9801b48 + d8f6549), 2026-09-16

**Verdict: PASS (0 high, 0 medium, 1 low)**

Binary: PASS. This is the re-sweep of the two fix commits 9801b48 ("add biorx/1.0
UA to monitor.py + fix pdf_handler default; repair test isolation") and d8f6549
("fix: Optional[str] annotation + regression test for PDFHandler default dir"),
run per the P26 corollary (fix commits are unreviewed code and introduce defects
at roughly the rate they remove them). Both findings from the prior gate
(full-sweep-9801b48) are genuinely closed: F1 (PEP-604 `str | None` in
pdf_handler.py, which crashed `import src.pdf_handler` on the claimed 3.9 floor)
is now `Optional[str] = None` with `Optional` already imported; F2 (the
env-aware `default_pdf_dir()` constructor default had no regression test) now has
`test_h_pdf_handler_default_constructor_uses_env_aware_dir`, which is
provable-failing under mutation and writes only `tmp_path/pdfs` (P28-clean). The
cold reviewer raised one MEDIUM finding — that the `Optional[str]` fix was a lone
edit while PEP-604 `X | None` still lives in 7 siblings, keeping the 3.9 floor
broken app-wide — but direct verification downgrades it: 6 of the 7 cited files
(arxiv.py, socarxiv.py, cache.py, biorxiv_medrxiv.py, psyarxiv.py, europepmc.py)
all carry `from __future__ import annotations`, so their `X | None` annotations
are lazy strings that never evaluate on 3.9; the 7th (tests/conftest.py:43) is
test-only and never ships to 3.9. The single genuinely 3.9-breaking instance —
`agents/monitor.py:47` `-> dict | None`, which has no future import — is
pre-existing (not introduced by this range), already tracked in TODO.md:183, and
was explicitly scoped out of the prior gate's F1 as already-flagged. It stays a
TODO item; it is not a defect of these commits. One LOW cosmetic finding
(redundant `reload` + misleading comment) is deferrable. Suite green and
deterministic: 530 passed, 19 skipped.

---

## Report

RANGE:       `git diff 607f1ed...HEAD` — the two fix commits. The caller named
             "9801b48..HEAD (the two fix commits d8f6549 and its parent)". The
             literal two-dot `9801b48..HEAD` resolves to only d8f6549 (9801b48 is
             its parent, so excluded); the parenthetical makes the intent
             unambiguous — review both fix commits d8f6549 and 9801b48 — so the
             base is 607f1ed (9801b48's parent). Materialized to /tmp/sweep.diff
             (86 lines; 3 files: agents/monitor.py, src/pdf_handler.py,
             tests/test_h_environment.py); reproducible via
             `git diff 607f1ed...HEAD`. Step-1 rule #1 (caller-supplied range),
             base corrected to match the stated "two fix commits".

COMMITS:     2 — 9801b48 ("add biorx/1.0 UA to monitor.py + fix pdf_handler
             default; repair test isolation"), d8f6549 ("fix: Optional[str]
             annotation + regression test for PDFHandler default dir"). Both
             audited line-by-line as unreviewed fix code.

APPLICABLE:  P4, P5, P10, P27, P28, P29, P34, P19-corollary (8 patterns).

CHECKED:     F1 close verified — src/pdf_handler.py:37 is now
             `def __init__(self, output_dir: Optional[str] = None)`; `Optional`
             is imported (line 8) and already used at line 113 (`Optional[int]`);
             no `str | None` remains in the module. F2 close verified — the new
             test `test_h_pdf_handler_default_constructor_uses_env_aware_dir`
             (tests/test_h_environment.py:521) monkeypatches `DATA_DIR` to
             `tmp_path`, constructs bare `PDFHandler()`, asserts
             `output_dir == tmp_path / "pdfs"` (exact, not a floor — P29-clean).
             P27 mutation (verbatim lines): default-ctor test — reverting
             pdf_handler.py:44 to `Path(output_dir).expanduser()` yields
             `Path(None)` → TypeError → red; replacing `default_pdf_dir()` with
             `DEFAULT_PDF_DIR` yields `~/preprints/PDFs` ≠ `tmp_path/pdfs` → red.
             monitor test — deleting the `headers=` kwarg (monitor.py:128) makes
             `fake_get` receive `headers=None` → `.get("User-Agent")` on None →
             AttributeError → red. Both are behavioural kwarg/value intercepts,
             not source-text greps (P19-corollary clean). P28/P34 — the
             default-ctor test writes only `tmp_path/pdfs`; the monitor test
             stubs `requests.get` and writes `tmp_path/unknown.pdf`; the conftest
             fingerprint guard over `~/preprints/` plus `BIORX_DB_PATH` /
             `BIORX_CACHE_PATH` redirects remain in force; no bare-`PDFHandler()`
             construction exists elsewhere in the suite (grep: only the new test,
             and test_h_environment.py:514 passes `output_dir=str(tmp_path)`).
             P10 fix→test map — monitor UA fix → `test_h_monitor_pdf_download_sends_biorx_user_agent`;
             pdf_handler default fix → `test_h_pdf_handler_default_constructor_uses_env_aware_dir`.
             P4/P5 — the two PDF-download siblings (pdf_handler.py:94,
             monitor.py:128) both hardcode `biorx/1.0`; all search/metadata
             adapters use `polite_user_agent()`. The prior gate's F3 (hardcoded UA
             vs `polite_user_agent()`) remains deferred as left — not re-litigated.

NOT COVERED: no caller-excluded paths (full 2-commit range). The caller's literal
             `9801b48..HEAD` was widened to `607f1ed...HEAD` to match the stated
             "two fix commits" intent — the widening is documented here, not
             silent. Live HTTP not exercised (adapter tests are stub/fixture unit
             tests). Scope-limit families NOT assessed: logic/algorithmic
             correctness, concurrency/races, authn/authz, injection/security,
             performance, dependency/supply-chain, API-contract compatibility,
             architecture, test quality beyond P27/P28/P29/P32, and UI-regression
             (PyQt6). Pre-existing `monitor.py:135` download-failure silence
             (DEBUG log, discarded return) is in TODO.md and was not audited as a
             diff finding. This repo has NO ./LEARNINGS.md (re-verified) — the
             sweep ran against the generic P1–P35 catalogue in
             ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (530 passed, 19 skipped, 2 warnings, ~7.7 s). Deterministic; judged by
             exit code (P24), no flake observed. Warnings are benign
             (httpx/Starlette deprecation, HTTP_413 constant), unrelated to this
             range.

---

## FINDINGS

F1 · P32-corollary · tests/test_h_environment.py:528-530 · LOW (high confidence)
  `reload(ph_mod)` with the comment "pick up the fresh env variable" is a no-op:
  `default_pdf_dir()` reads `os.environ` at call time, not import time, so the
  reload changes nothing — the monkeypatched `DATA_DIR` is visible without it.
  The comment asserts a false reason for the reload. Fix: delete the reload (and
  the now-false comment), or drop only the comment so no one copies a false
  rationale. Deferrable; the test passes and is correct either way.

---

## Verdict note

0 high, 0 medium, 1 low. F1 and F2 from the prior gate are closed correctly and
their tests are provable-failing and isolated. The cold reviewer's one MEDIUM
candidate was downgraded on verification: it cited 7 sibling `X | None` sites as
3.9-breaking, but 6 of them are protected by `from __future__ import annotations`
and the 7th is test-only; the single real instance (`agents/monitor.py:47`
`-> dict | None`, no future import) is pre-existing and already tracked in
TODO.md:183, so it is not a defect introduced by this range. It remains an open
TODO — the claimed 3.9 floor is still broken by monitor.py regardless of
pdf_handler, and closing that (either sweeping the class or declaring and
enforcing a 3.11+ floor, since the suite runs on 3.11 and the app on 3.12) is the
correct next action, but it predates these commits and was never in their scope.
The one LOW finding is cosmetic and deferrable. This range is clean against
P1–P35 (8 applicable).
