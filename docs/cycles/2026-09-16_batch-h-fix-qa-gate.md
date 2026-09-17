# Learning-QA gate — batch-h fix (a0e5edc), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. The fix commit's own headline claim — "F1-F3 fixed" — is not
met. F1 was "contact email is half-wired class-wide"; the fix rewired only
OpenAlex (`src/paper_meta.py`), leaving arXiv at `polite_user_agent({})`
unchanged from e1fea58, so a config-only user still sends no mailto on 1 of the
4 polite-pool consumers. And F3 was "the no-personal-address guarantee is
narrower than the removal"; the fix deleted `ARXIV_ADAPTER_TASK.md` but left
`davegalloway@me.com` and `davebgalloway@gmail.com` in the *tracked*
`docs/cycles/2026-09-15_chunk2-qa-gate.md`, carving `docs/` out of the scan so
the guarantee reads green over a tree that still ships the address. The suite is
green and deterministic (515 passed) — but the feature is incomplete as shipped.

---

## Report

RANGE:       `git diff e1fea58...HEAD` (three dots; e1fea58 is the direct parent,
             so this equals `e1fea58..HEAD` = one commit). Materialized to
             /tmp/sweep.diff; reproducible via `git diff e1fea58...HEAD`.

COMMITS:     1 — a0e5edc "fix: chdp F1-F3 (contact email wiring, web warnings,
             md scan)". 5 files, +68/-5: src/paper_meta.py, web/app.py,
             web/deps.py, tests/test_h_environment.py, tests/web/test_app.py.

APPLICABLE:  P2 (silent drop — arXiv mailto), P3 (narrow scope — md scan
             carve-out), P5 (sibling hardening — 4 polite-pool consumers),
             P10 (test effort on the easy case — env-only arXiv test),
             P19 (comment/contract drift — sources_config.yaml), P25
             (front-end wiring — web warnings), P31 (absence-proof over a
             narrowed population — docs/ excluded from the scan).

CHECKED:     P1, P2, P3, P4, P5, P6, P8, P10, P19, P21, P22, P25, P27, P28,
             P31, P34, P35 — read the actual files and their call sites, not
             just the diff (arxiv.py, config.py, orchestrator.py, paper_meta.py,
             web/deps.py, web/app.py, both test files, sources_config.yaml).
             P7/P9/P11–P18/P20/P23/P24/P26/P29/P30/P32/P33 not applicable: no
             scoring/ranking, LLM, background-worker, or graph/traversal code in
             this range.

NOT COVERED: untracked files not reviewed (`git diff` only). Scope-limit
             families NOT assessed: logic/algorithmic correctness,
             concurrency/races, authn/authz, injection/security, performance,
             dependency/supply-chain, API contract compatibility, architecture,
             and test quality beyond P27/P28/P29/P32. This repo has NO
             `./LEARNINGS.md` (verified) — the sweep ran against the generic
             P1–P35 catalogue only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (515 passed, 19 skipped, ~7.4 s). Deterministic; judged by exit
             code (P24).

---

## FINDINGS

F1 · P5/P25/P10 · src/sources/arxiv.py:116 · MEDIUM (high confidence)
  The contact-email fix is still half-wired class-wide. `openalex_user_agent()`
  (paper_meta.py:42) now loads `sources_config.yaml` and falls back to it, but
  `arxiv.py:116` still calls `polite_user_agent({})` — `git diff e1fea58 HEAD --
  src/sources/arxiv.py` is empty, so the arXiv path is exactly as it was when
  the original gate flagged it. A config-only user (contact_email in
  sources_config.yaml, no BIORX_CONTACT_EMAIL) now sends a mailto on Crossref,
  Unpaywall, and OpenAlex, but NOT on arXiv — the precise F1 defect this commit
  claims to fix. The test `test_h_arxiv_request_carries_the_contact_address`
  (test_h_environment.py:161) exercises only the env path, so it stays green
  while the config path stays broken (P10). Secondary note: `openalex_user_agent`
  re-reads the config from a CWD-relative path on every call — a server started
  outside the repo root silently falls back to the empty default and drops the
  mailto; the `try/except Exception` around `load_sources_config()` is
  unreachable (that function never raises). Fix: thread the loaded config
  through `ArxivAdapter` (as Crossref/Unpaywall already do) and add a
  config-only test asserting the mailto appears; resolve the config path
  relative to the repo, not CWD.

F2 · P31/P3 · tests/test_h_environment.py:38-44 + docs/cycles/2026-09-15_chunk2-qa-gate.md:60,248 · MEDIUM (high confidence)
  The "no personal address in source" guarantee remains narrower than the
  committed tree. `_root_md_files()` scans only `ROOT.glob("*.md")`, so the new
  markdown check never looks inside `docs/` at all — a carve-out broader than
  the docstring's stated reason ("docs/cycles/ holds historical gate files").
  The tracked file `docs/cycles/2026-09-15_chunk2-qa-gate.md` still contains
  `davegalloway@me.com` (line 60) and `davebgalloway@gmail.com` (line 248).
  Deleting `ARXIV_ADAPTER_TASK.md` removed one leak, but the batch-h objective
  "nothing personal ships in the source" is still false against the committed
  tree, and the test name `test_h_no_personal_address_in_source` overstates its
  scope. Fix: redact/rewrite the addresses in the committed gate files (or move
  audit quotes out of the tracked tree), and either rename the guarantee to its
  real scope or scan `docs/` with an explicit allowlist of the remaining audit
  lines.

F3 · P19 · sources_config.yaml:43-45 · LOW (high confidence)
  Stale user-facing comment. It still reads "OpenAlex and arXiv read only the
  environment variable (they are called without the loaded config)." After this
  commit OpenAlex DOES read the file, so the comment is half-wrong and misleads
  a config-only user into believing their address is ignored by OpenAlex. Fix:
  update the comment (after F1 is fixed, the OpenAlex/arXiv distinction can be
  dropped entirely).

F4 · P2/robustness · web/app.py:67 · LOW (medium confidence)
  `/healthz` now calls `c.get_orchestrator()`, so the liveness probe constructs
  the whole orchestrator and imports every enabled adapter — the exact coupling
  the lazy-construction docstring (deps.py:53-55) was written to avoid ("an
  unrelated source stop the app from booting"). An unrelated adapter import
  failure now 500s the liveness probe. The wiring is correct and tested; the
  cost is a heavier, more fragile liveness path. Consider building the
  orchestrator once at startup and surfacing warnings from there instead.

---

## Verdict note

4 findings — 2 medium (F1, F2), 2 low (F3, F4), 0 high. Not clean. The web
warnings half (F2 of the original gate) is genuinely fixed and well-tested, and
the OpenAlex rewiring is correct in itself — but it is only 1 of the 2 call
sites F1 named, and the personal address the guarantee is meant to keep out of
the source is still in the committed tree. Fix F1 (arXiv config) and F2
(redact/relocate the committed addresses) and re-gate the fix commit as its own
range before this merges.
