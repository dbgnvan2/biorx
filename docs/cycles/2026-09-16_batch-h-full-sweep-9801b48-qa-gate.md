# Learning-QA gate — batch-h (re-sweep of fix commit 9801b48), 2026-09-16

**Verdict: REJECTED (0 high, 2 medium, 1 low)**

Binary: REJECTED. This is the re-sweep of the fix commit 9801b48 (P26 corollary:
fix commits are unreviewed code). F1 from the prior gate is genuinely closed —
`monitor.py` download now sends a descriptive UA and its new test is
provable-failing under mutation — but the fix commit introduced one new defect
and left the other fix unguarded. The two MEDIUM findings both live in
`9801b48` itself: (F1) the F2 fix changed `PDFHandler.__init__` to use a PEP-604
`str | None` annotation, a Python 3.10+ construct that evaluates at class-
definition time and hard-crashes `import src.pdf_handler` on Python 3.9 — the
exact floor CLAUDE.md claims to support, and the same class TODO.md:183 already
flags (`dict | None` in monitor.py); and (F2) the higher-stakes half of the
commit — bare `PDFHandler()` now resolving via the env-aware `default_pdf_dir()` —
has no regression test, so reverting it stays green. One LOW finding (F3) is a
hardcoded `biorx/1.0` rather than `polite_user_agent()`, mitigated because the
sibling `pdf_handler.download_pdf` hardcodes the same string and the prior gate
explicitly accepted `"biorx/1.0"` as the close condition for the download class.
Suite is green and deterministic: 529 passed, 19 skipped.

---

## Report

RANGE:       `git diff origin/main..HEAD` — caller-supplied range, used exactly
             (step-1 rule #1). Base `origin/main` = `5ffad2e`, HEAD = `9801b48`.
             The base is the merge-base, so two-dot and three-dot are identical.
             Materialized to /tmp/sweep.diff (2,804 lines, 35 files); reproducible
             via `git diff origin/main..HEAD`. This pass is the re-sweep of the
             prior gate's fix commit `9801b48`; its monitor.py / pdf_handler.py /
             test changes are audited as ordinary unreviewed code (P26 corollary).

COMMITS:     31 — 5 docs, 1 feat (batch-h), 20 fix, 5 chore (BASELINE advances).
             The one NEW unreviewed commit is 9801b48 ("fix: add biorx/1.0 UA to
             monitor.py + fix pdf_handler default; repair test isolation"); it was
             audited line-by-line. The other 30 commits passed the prior
             full-sweep-607f1ed cold pass and were re-read as a batch here.

APPLICABLE:  P4, P5, P10, P27, P28.

CHECKED:     F1 close verified — agents/monitor.py:128 sends
             `headers={"User-Agent": "biorx/1.0"}`; the new test
             `test_h_monitor_pdf_download_sends_biorx_user_agent` intercepts the
             `requests.get` call and asserts the kwarg. P27 mutation reasoned on
             the verbatim line: deleting the `headers=` kwarg makes `fake_get`
             receive `headers=None` → `captured["headers"]=None` →
             `.get("User-Agent")` on None raises AttributeError → red. It is a
             behavioural kwarg-intercept, not a source-text grep (P19-corollary
             clean). P5 class-wide — grepped every `requests.get`/`Session` and
             `User-Agent` literal across src/, agents/, web/, gui.py: all seven
             search adapters + paper_meta OpenAlex/Europe PMC/Crossref recovery +
             SearchAgent + unpaywall use `polite_user_agent()`; the two PDF-
             download siblings (pdf_handler.py:94, monitor.py:128) both hardcode
             `biorx/1.0` — mutually consistent, but see F3. No remaining bare
             polite-pool download. P10 fix→test map — F1 has a test; F2 does not
             (F2). P4/floor — the fix introduced `str | None` at pdf_handler.py:37
             in a module with no `from __future__ import annotations` (F1). P28 —
             the new test passes `tmp_path` as dest_dir and never constructs bare
             `PDFHandler()`, so no production-path write; the conftest fingerprint
             guard is present. The "repair test isolation" claim in the commit
             message is sound: `agents/monitor.py` imports orchestrator/config/
             filtering at module level, never `src.db`, so dropping the
             `sys.modules["src.db"]` stub is correct and importing `agents.monitor`
             in a test cannot reach production data.

NOT COVERED: no caller-excluded paths (full branch). Untracked / working-tree
             files NOT in the range: .test-qa-report.md, Social Baseline
             Theory.xlsx, the untracked docs/cycles/2026-09-16_batch-h-*
             -qa-gate.md files, docs/epigenetic-inheritance-review/, and the
             staged deletion of ARXIV_ADAPTER_TASK.md. Docs reviewed only for
             personal-address leakage, not content. Live HTTP not exercised
             (adapter tests are mock/recorded-fixture unit tests). Scope-limit
             families NOT assessed: logic/algorithmic correctness,
             concurrency/races, authn/authz, injection/security, performance,
             dependency/supply-chain, API-contract compatibility, architecture,
             test quality beyond P27/P28/P29/P32, and UI-regression (PyQt6). The
             pre-existing P2 silence of monitor.py:135 (download failure logged at
             DEBUG, return value discarded) is already recorded in TODO.md and was
             not audited as a diff finding. This repo has NO ./LEARNINGS.md
             (re-verified) — the sweep ran against the generic P1–P35 catalogue in
             ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (529 passed, 19 skipped, 2 warnings, ~7.6 s). Deterministic; judged by
             exit code (P24), no flake observed. Warnings are benign
             (httpx/Starlette deprecation, HTTP_413 constant), unrelated to this
             range.

---

## FINDINGS

F1 · P4 · src/pdf_handler.py:37 · MEDIUM (high confidence)
  The F2 fix changed the signature to `def __init__(self, output_dir: str | None
  = None)`. `str | None` is a PEP-604 union that evaluates at class-definition
  time; with no `from __future__ import annotations` in the module, `import
  src.pdf_handler` raises `TypeError: unsupported operand type(s) for |` on
  Python 3.9 — contradicting CLAUDE.md's "Python 3.9+ syntax" floor. Nothing
  catches it because the suite runs under 3.11 and the app under 3.12. This is a
  new instance of the class TODO.md:183 already documents (`dict | None` in
  monitor.py). The file already imports `Optional` (used at line 113
  `Optional[int]`). Fix: `def __init__(self, output_dir: Optional[str] = None)`.

F2 · P10 · src/pdf_handler.py:37 (the F2 fix) · MEDIUM (high confidence)
  The behavioral fix — bare `PDFHandler()` now resolving `output_dir` via
  `default_pdf_dir()` so it honors `DATA_DIR` — has no regression test. The only
  related test (tests/web/test_deploy_files.py:448-458) pins `default_pdf_dir()`
  itself, not the constructor default, exactly as the prior gate's F2 finding
  warned. Reverting `output_dir or default_pdf_dir()` to `DEFAULT_PDF_DIR` stays
  green. Fix: add a test that monkeypatches `DATA_DIR` to a tmp path and asserts
  `PDFHandler().output_dir == Path(tmp) / "pdfs"` (plus the delenv fallback to
  `DEFAULT_PDF_DIR`) — monkeypatched, not bare, so construction's `mkdir` never
  writes the real `~/preprints/PDFs` (P28).

F3 · P5 · agents/monitor.py:128 · LOW (low confidence)
  The UA is hardcoded `"biorx/1.0"` rather than resolved through
  `polite_user_agent(load_sources_config())` like the metadata/search siblings
  (search_agent.py:48, arxiv.py:118, paper_meta.py:193), so a configured
  `BIORX_CONTACT_EMAIL` never reaches the PDF-download request. Mitigated: the
  sibling `pdf_handler.download_pdf` (src/pdf_handler.py:94) hardcodes the same
  string, and the prior gate's F1 finding explicitly accepted `"biorx/1.0"` as
  the close condition for the download class — so the two PDF-download siblings
  are mutually consistent. Not blocking; record in TODO.md if the download class
  should ever carry a mailto.

---

## Verdict note

0 high, 2 medium, 1 low. F1 from the prior gate is closed correctly and its test
mutation-fails. The gate is REJECTED on the two MEDIUM findings, both in the fix
commit itself — the exact defect shape the re-sweep exists to catch (P26
corollary: a fix pass introduces defects at roughly the rate it removes them).
Both are trivial and in-loop: F1 is a one-word change (`Optional[str]`), F2 is a
~5-line test. F3 is LOW and deferrable. Recommend fixing F1+F2 in one commit and
re-sweeping that fix commit as its own range before push, consistent with how
regate5's and full-sweep-607f1ed's blockers were closed. Do not read this
REJECTED as "the contact-email/P5 objective is unmet" — it is met and verified;
the rejection is specifically that the newest fix commit carries a 3.9-floor
regression and an unpinned behavioral fix.
