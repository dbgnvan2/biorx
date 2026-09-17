# Learning-QA gate — batch-h (cold full sweep at HEAD 607f1ed), 2026-09-16

**Verdict: REJECTED (1 high, 1 medium, 1 low)**

This is a cold pass over the full 30-commit range, run with no knowledge of
the prior gate history (P26: a falling warm-review count is not a stopping
condition, so one cold pass is required regardless). The contact-email/P5
wiring the batch was building — sources_config + polite_user_agent threaded
through every metadata/search consumer (orchestrator's seven adapters, paper_meta's
OpenAlex / Europe PMC / Crossref recovery, SearchAgent, monitor search, GUI, web)
— holds up under inspection. But the cold reviewer surfaced one HIGH gap the
prior warm passes had walked past: the a928df6 fix commit that added the
`biorx/1.0` UA to "PDF downloads" fixed `pdf_handler.download_pdf` only, leaving
`monitor.py:download_pdf` — a sibling polite-pool download to the same hosts —
still on the bare `python-requests/x.y` UA. That is a P5 class-fix incompleteness
in the newest fix commit itself, so the gate is not clean.

---

## Report

RANGE:       `git diff origin/main..HEAD` (HEAD = 607f1ed). Selected by
             step-1 rule #1 (caller-supplied range, used exactly). Two-dot and
             three-dot are identical because origin/main is an ancestor of HEAD.
             Materialized to /tmp/sweep.diff (2,750 lines, 34 files);
             reproducible via `git diff origin/main..HEAD`.

COMMITS:     30 — 5 docs, 1 feat (batch-h), 19 fix, 5 chore (BASELINE advances).
             The three newest commits are the least-reviewed surface and were
             audited line-by-line (P26 corollary): 607f1ed, 098cc65, a928df6.
             The other 27 commits have passed prior review passes and were read
             as a batch here.

APPLICABLE:  P2, P4, P5, P6, P19, P21, P25, P26, P27, P28, P31, P34, P35.

CHECKED:     P5 — grepped every `requests.get/post/Session` and every
             `User-Agent` literal across src/, agents/, web/, gui.py and traced
             each polite-pool consumer to `polite_user_agent()`; the metadata/
             search class is closed, but one download sibling (monitor.py) is
             bare — F1, and the pdf_handler constructor default bypasses
             `default_pdf_dir()` — F2. P2 — traced every warning/drop path
             (orchestrator warnings, SearchAgent, app.js catch, monitor download,
             paper_meta recovery); recovery helpers load config but never warn on
             empty email — F3. P4 — grepped for hardcoded emails/version literals;
             none remain in source (biorx/1.0 is a deliberate product identity,
             not a personal address). P6/P21/P25 — read orchestrator
             `_register_adapters`, web/app.py /healthz, web/deps.py, gui.py
             `_show_startup_warnings`, app.js boot(); the startup_warnings
             surfacing chain is wired end-to-end (orchestrator appends → deps
             stores → /healthz returns → GUI statusBar shows → app.js now reads).
             P19/P27 — the new source-text JS test asserts the app.js wiring
             behaviourally (reads the served script's actual behaviour, not a
             comment substring); the drift-test guard-the-guards changed skip→fail
             so a bad BASELINE no longer hides. P28/P34 — conftest fingerprint
             guard returns None on a partial rglob walk and fails on None;
             `Database` honors BIORX_DB_PATH so the session-scope redirect is
             effective. P31/P35 — `_fingerprint_dir` (None-on-partial-snapshot)
             and the re-baselined drift test carry the completeness/availability
             as data rather than assuming it. P1/P3/P7/P9/P10 no new instances in
             range; each fix in the three newest commits carries a test
             (fix→test map satisfied).

NOT COVERED: no caller-excluded paths (full branch reviewed). Untracked /
             working-tree files NOT in the range: .test-qa-report.md, Social
             Baseline Theory.xlsx, the untracked docs/cycles/2026-09-16_batch-h-*
             -qa-gate.md files, docs/epigenetic-inheritance-review/, and the
             staged deletion of ARXIV_ADAPTER_TASK.md. Docs reviewed only for
             personal-address leakage, not content. Live HTTP not exercised
             (adapter tests are mock/recorded-fixture unit tests). Scope-limit
             families NOT assessed: logic/algorithmic correctness,
             concurrency/races, authn/authz, injection/security, performance,
             dependency/supply-chain, API-contract compatibility, architecture,
             test quality beyond P27/P28/P29/P32. `agents/monitor.py` was
             inspected only for the P5 sibling-class check; its pre-existing P2
             silence (download failure logged at DEBUG, caller discards the
             return) is already recorded in TODO.md and was not audited as a diff
             finding. This repo has NO ./LEARNINGS.md (re-verified) — the sweep
             ran against the generic P1–P35 catalogue in
             ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (528 passed, 19 skipped, 2 warnings in ~7.4 s). Deterministic;
             judged by exit code (P24), no flake observed. Warnings are benign
             (httpx/Starlette deprecation, HTTP_413 constant), unrelated to this
             range.

---

## FINDINGS

F1 · P5 · agents/monitor.py:128 · HIGH (high confidence)
  `download_pdf` does `requests.get(pdf_url, timeout=timeout)` with no
  `headers`, so the headless CLI's PDF download path sends requests' default
  `python-requests/x.y` User-Agent to bioRxiv/medRxiv/arXiv/PMC hosts. The
  a928df6 fix commit ("add biorx/1.0 UA to PDF downloads") hardened only
  `pdf_handler.download_pdf` (src/pdf_handler.py:92-94) and left this sibling
  download — the same class, the same hosts — bare. This is the exact P5
  half-fix shape this batch spent five gates closing, and it lives in the
  newest fix commit itself (P26 corollary). Fix: add
  `headers={"User-Agent": polite_user_agent(cfg)}` (or `"biorx/1.0"`) and a
  capture test asserting the outgoing header, mirroring the pdf_handler test.

F2 · P5/P28 · src/pdf_handler.py:37 · MEDIUM (medium confidence)
  `PDFHandler.__init__` defaults `output_dir=DEFAULT_PDF_DIR` (`~/preprints/PDFs`),
  bypassing the env-aware `default_pdf_dir()` that
  `web/routes_summaries.py:90` calls explicitly. So bare `PDFHandler()`
  constructions — `agents/summarization_agent.py:42` and `gui.py:278,317` —
  still write to the hardcoded path and ignore `DATA_DIR`, the exact bug
  `default_pdf_dir()`'s docstring documents as fixed (container PDFs landing
  in the ephemeral home dir). Pre-existing, surfaced by the in-range
  pdf_handler UA hunk. Fix: change the constructor default to
  `default_pdf_dir()` so every call site honors the env override; the existing
  `test_deploy_files.py` assertion only pins `default_pdf_dir()` itself, not
  the constructor default, so it does not pin the bug.

F3 · P2/P25 · src/paper_meta.py:37,265,278 · LOW (low confidence)
  The three standalone recovery helpers (`openalex_user_agent`, `_europepmc`,
  `_crossref_abstract`) load sources_config but never warn when no contact
  email is configured, so a CLI-only summarize path degrades silently even
  though the orchestrator warning covers GUI/web and SearchAgent covers CLI
  search. Already recorded as deferred in TODO.md:8-14 (primary orchestrator
  warning fires; per-call warnings would be noisy in batch runs). Fix: add a
  `get_contact_email()` check + `log.warning` in each, or keep the documented
  deferral and close it in the next batch.

---

## Verdict note

1 high, 1 medium, 1 low. The range's core objective — contact email threaded as
a polite User-Agent across every polite-pool metadata/search consumer — is met
and verified end-to-end, and the P28/P31 fingerprint guard, the P27
mutation-checked JS wiring test, and the P25 GUI/web/CLI surfacing chain all
hold up under a cold inspection. The gate is REJECTED on F1: a one-line fix plus
a capture test in `monitor.py` that closes the download-path sibling the newest
fix commit left open. F2 is a one-line constructor-default change. F3 is already
in the backlog. Recommend: fix F1 (and F2) in the next loop, then re-sweep the
fix commits as their own range before pushing — consistent with how regate5's
F1/F2 were closed. Do not treat this pass as "the P5 class is closed end-to-end":
say "closed for metadata/search APIs; monitor.py's download path still bare
(see F1)."
