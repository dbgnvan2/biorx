# Learning-QA gate — batch-h (contact email from env, CI), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. Three medium findings touch the chunk's own deliverable, and
one of them falsifies its headline claim. The chunk's guarantee — "nothing
personal ships in the source" — is narrower than its removal: the enforcing test
scans only Python files, while `ARXIV_ADAPTER_TASK.md:35` still instructs
`mailto:davegalloway@me.com`. The contact-email extraction is also only
half-wired: `polite_user_agent({})` passes an empty dict at the arXiv and
OpenAlex call sites, so a config-only user sends no mailto on 2 of the 4
polite-pool consumers. And the new "Unpaywall off" warning reaches only the GUI
(retiring per GL.5); the web app builds the orchestrator but nothing in `web/`
reads `.warnings`. Nothing here loses data or blocks the suite — it is green and
deterministic — but the feature is incomplete as shipped.

---

## Report

RANGE:       `git diff origin/main...HEAD` (three dots, merge base 5ffad2e) —
             selected by step-1 rule #1 (caller-supplied range, used exactly).
             Materialized to /tmp/sweep.diff; reproducible via the same command.

COMMITS:     15 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact
             email from env, no personal address in source, CI); 9 fix
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58: test-qa loop findings — P28 DB redirect +
             fingerprint guard, P21 dead code, P27/P35/P19/P31/P5).

APPLICABLE:  P2 (silent drop — Unpaywall off), P3 (narrow scope — single-platform
             CI, one contact source), P4 (hardcoded `~/preprints`), P5 (sibling
             hardening — contact email across 4 consumers), P19 (prose/comment vs
             parsed assertions; baseline drift), P21 (built-not-wired —
             SearchCache), P25 (front-end wiring — warnings), P27/P28 (can't-fail
             tests / default paths), P31 (partial snapshot), P34 (entry-point side
             effects), P35 (baseline/diff provenance).

CHECKED:     P1, P2, P3, P4, P5, P6, P8, P19, P21, P22, P25, P27, P28, P31, P34,
             P35 (read the actual files and their callers, not just the diff).
             P7/P9/P10/P11–P18/P20/P23/P24/P26/P29/P30/P32/P33 not applicable:
             no scoring/ranking, LLM, background-worker, or graph/traversal code
             in this range.

NOT COVERED: no caller-excluded paths. Untracked files not reviewed (git diff
             only). Docs-only changes (specs, guides, plans) reviewed only for
             personal-address leakage, not content. Scope-limit families NOT
             assessed: logic/algorithmic correctness, concurrency/races,
             authn/authz, injection/security, performance, dependency/supply-chain,
             API contract compatibility, architecture, and test quality beyond
             P27/P28/P29/P32. This repo has NO `./LEARNINGS.md` (verified) — the
             sweep ran against the generic P1–P35 catalogue only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (512 passed, 19 skipped, ~7.4 s). Deterministic; judged by exit code
             (P24), no flake observed.

---

## FINDINGS

F1 · P5 · src/paper_meta.py:38 + src/sources/arxiv.py:116 · MEDIUM (high confidence)
  `contact_email` set in sources_config.yaml never reaches arXiv or OpenAlex.
  Both call sites pass `polite_user_agent({})` (an empty dict), so only the
  `BIORX_CONTACT_EMAIL` env var works there; a config-only user silently sends
  no mailto on 2 of the 4 polite-pool consumers (Crossref and Unpaywall DO get
  the loaded config via orchestrator.py:113 and :117). The batch-h feature is
  half-wired class-wide. Fix: thread the loaded config into these two call
  sites (pass `self.config`/`sources_config` through `ArxivAdapter` and
  `openalex_user_agent`), or drop the config fallback from `get_contact_email`
  and document env-only.

F2 · P25/P2 · src/sources/orchestrator.py:129 + web/deps.py:59 · MEDIUM (high confidence)
  The "Unpaywall off, no contact email" warning is appended to
  `self.warnings` (orchestrator.py:129) but read only by gui.py:1978, which is
  frozen/retiring per GL.5. The web app builds `SourceOrchestrator` via
  `AppContext.get_orchestrator()` (deps.py:59) and nothing in `web/` reads
  `.warnings` — so a web user gets a silent, log-only drop of open-access
  lookup. (The test docstring at test_h_environment.py:171 even claims "callers
  (GUI, web app) can surface" — the web half does not exist.) Fix: surface
  `orchestrator.warnings` in a startup/status route (or settings badge) with a
  web-level test that a no-email context renders the message.

F3 · P3/P31 · tests/test_h_environment.py:29 + ARXIV_ADAPTER_TASK.md:35 · MEDIUM (high confidence)
  The "no personal address in source" guarantee is narrower than the removal it
  claims to enforce. `SCANNED_DIRS = ["src", "web", "agents"]` (+ gui.py) scans
  only Python files, but `ARXIV_ADAPTER_TASK.md:35` still instructs
  `mailto:davegalloway@me.com` (and docs/cycles/2026-09-15_chunk2-qa-gate.md:60
  names the same address). The chunk's headline objective — nothing personal
  ships in the source — is not met while that task file is checked in. Fix:
  extend the scan to `.md` under `docs/` and the repo root, or delete/stub the
  stale task file so the guarantee matches the removal's intent.

F4 · P3 · .github/workflows/tests.yml:11 · LOW (medium-low confidence)
  CI runs `ubuntu-latest` only, contradicting the amended backlog §6
  (macOS+Windows matrix) and V3 ("FTS5 available on both"), so
  `test_h_sqlite_has_fts5` only ever proves FTS5 on Linux. Fix: implement the
  macOS+Windows matrix the plan already specifies, or amend the plan to say
  Linux-only is a deliberate decision.

F5 · P4/P19 · src/sources/cache.py:29-37 · LOW (high confidence it is a copy; low severity while dead)
  `_default_cache_path()` is a hand-maintained copy of src/db.py's default-path
  resolution and adds a 4th hardcoded `~/preprints` literal. It will drift when
  GL.1 centralizes path resolution (platformdirs). Fix: when SearchCache is
  wired, resolve its path through the same single helper as the DB (or delete
  the module until it has a caller).

F6 · P21/P28 · tests/conftest.py:82-91 · LOW (medium confidence)
  The `BIORX_CACHE_PATH` redirect is unexercised dead weight — SearchCache has
  zero callers (its own module docstring says so), so no test proves the
  redirect actually moves a write off `~/preprints`. Honestly documented, and
  harmless today, but the guard is unprovable until a test constructs
  `SearchCache()`. Fix: add one SearchCache test asserting the redirect moves
  the write off the real path.

---

## Verdict note

6 findings — 3 medium (F1, F2, F3), 3 low (F4, F5, F6), 0 high. Not clean. The
batch-h contact-email extraction is directionally correct and well-tested on the
two consumers that pass config, but it is half-wired class-wide (F1), the new
warning channel reaches only the GUI being retired (F2), and the "no personal
address" guarantee is narrower than the removal (F3). Fix F1–F3 and re-gate the
fix commit as its own range before this merges.
