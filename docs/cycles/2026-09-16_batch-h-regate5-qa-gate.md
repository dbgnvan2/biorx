# Learning-QA gate — batch-h (re-gate at HEAD 49ac308), 2026-09-16

**Verdict: APPROVED (with backlog — 0 high, 2 medium, 2 low)**

Binary: APPROVED. The two HIGH findings that rejected the prior gate
(regate4: F1 `SearchAgent` building `BioRxivAPI()` bare, F2 `_europepmc()`
building `EuropePmcAdapter()` bare) are closed in 49ac308. Both sites now
thread `sources_config` / `polite_user_agent` through the same guarded
`load_sources_config()` shape as their already-fixed siblings, and each fix
carries a behavioural test that mutates red. The polite-pool API class is now
closed for every metadata/search consumer (orchestrator's seven adapters,
paper_meta's OpenAlex / Europe PMC / Crossref recovery, SearchAgent, monitor,
GUI, web). This pass found 0 high. Two medium and two low findings remain —
all recorded below, none silently dropped; they do not block merge and are
deferred to the next batch consistent with how regate4's F3/F4 were deferred.

---

## Report

RANGE:       `git diff origin/main..HEAD` (27 commits, HEAD = 49ac308). Selected
             by step-1 rule #1 (caller-supplied range, used exactly). Two-dot and
             three-dot are identical here because origin/main is an ancestor of
             HEAD. Materialized to /tmp/sweep.diff (2,641 lines, 31 files);
             reproducible via `git diff origin/main..HEAD`.

COMMITS:     27 — 5 docs, 1 feat (batch-h), 15 fix, 5 chore (BASELINE advances),
             1 fix (49ac308, the F1/F2 close). Newest commit 49ac308 is the
             least-reviewed surface and was audited line-by-line (P26 corollary);
             the other 26 commits have passed prior review passes and were read
             as a batch here.

APPLICABLE:  P2, P4, P5, P6, P19, P21, P25, P27, P28, P31, P35.

CHECKED:     P5 (sibling hardening, class-wide: grepped every HTTP consumer
             across src/, agents/, web/, gui.py, test_components.py — all six
             search adapters + crossref + unpaywall + paper_meta recovery +
             SearchAgent now thread sources_config/polite_user_agent; two bare
             consumers remain and are findings F1/F4), P2 (read
             load_sources_config/polite_user_agent and every
             `except Exception: cfg={}` site; the except guards are effectively
             dead since load_sources_config never raises, so the only real
             fallback is the no-email bare-UA case — and the standalone CLI path
             surfaces no warning, F2), P4 (verified personal/placeholder address
             removal; found one stale count literal in a yaml comment, F3), P6
             (startup_warnings is a real derived list — orchestrator appends,
             web/deps stores, /healthz returns, GUI statusBar shows), P25
             (surfacing chain traced end-to-end: GUI wired, web /healthz exposes
             but web/static/app.js never reads — deferred F3; SearchAgent CLI has
             no surface, F2), P21 (SearchCache explicitly documented zero-callers
             rather than silently dead), P27 (both new tests mutation-reasoned
             red: deleting the wiring kwarg fails each assertion; bodies carry
             asserts, no bare returns), P28/P31/P35 (conftest fingerprint guard
             returns None on partial rglob walk and fails on None; drift-test
             `_git` changed skip→fail so a bad BASELINE no longer hides), P19/P24
             (success judged on exit code, not scraped tokens; BASELINE advances
             W1.a-documented). P1/P3/P7/P9/P10 no new instances in range; each of
             the two 49ac308 fixes carries a test (fix→test map satisfied).

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
             test quality beyond P27/P28/P29/P32. This repo has NO
             ./LEARNINGS.md (re-verified) — the sweep ran against the generic
             P1–P35 catalogue in ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (526 passed, 19 skipped, ~7.4 s). Deterministic; judged by exit
             code (P24), no flake observed. Two benign deprecation warnings
             (httpx/Starlette, HTTP_413 constant), unrelated to this range.

---

## FINDINGS

F1 · P5 · src/pdf_handler.py:92 · MEDIUM (medium confidence)
  `download_pdf` does `requests.get(url, timeout=timeout, stream=True)` with no
  `headers`, so PDF downloads to bioRxiv/medRxiv/arXiv/PMC hosts send requests'
  default `python-requests/x.y` User-Agent — no product identity, no mailto. The
  "P5 class closed" claim covers the metadata/search polite pools; the document-
  download path is a sibling HTTP consumer to the same hosts and is still bare.
  This is a different class than the polite-pool APIs (a content download, not
  an API metadata call), hence medium not high — but it is the same half-fix
  shape this batch spent five gates closing. Fix: give `download_pdf` a
  `polite_user_agent(load_sources_config())` header (or an explicit
  `User-Agent` header), plus a test asserting the outgoing header.

F2 · P2/P25 · agents/search_agent.py:39-43 (+ main) · MEDIUM (medium confidence)
  The standalone CLI path (`python agents/search_agent.py --cluster X`) now
  sends `biorx/1.0` bare when no contact email is configured — correct and
  consistent with every other path — but surfaces no warning, unlike the
  orchestrator (warnings list), GUI (status bar), and web (/healthz). F3's deferral
  covered only the web-frontend half of surfacing; the CLI half is untracked, so
  a CLI-only user runs silently degraded with no nudge to set BIORX_CONTACT_EMAIL.
  Fix: in `main()`/`__init__`, log a warning when
  `get_contact_email(sources_cfg)` is empty, mirroring the orchestrator message.

F3 · P4 · sources_config.yaml:43-44 · LOW (low confidence)
  The comment claims "all four sources" read contact_email, but the address now
  feeds eight consumers (Crossref, OpenAlex, arXiv, Europe PMC/PubMed, PsyArXiv,
  SocArXiv, bioRxiv/medRxiv, Unpaywall). Stale count that misleads the next
  person adding a source. Fix: reword to "all polite-pool consumers".

F4 · P5 · test_components.py:96 · LOW (low confidence)
  The manual smoke script still constructs `BioRxivAPI()` bare and hits the live
  bioRxiv API with no identifying UA. Dev-only (not on the run path, not in the
  pytest suite), so low — but it is the last bare `BioRxivAPI()` in the tree.
  Fix: pass `polite_user_agent(load_sources_config())`.

---

## Verdict note

0 high, 2 medium, 2 low. The range's core objective — contact email threaded as
a polite User-Agent across every polite-pool metadata/search consumer — is met,
and the two HIGH blockers from regate4 are closed with tests and a green,
deterministic suite. The four remaining findings are non-blocking: F1 is a real
P5 gap in the *sibling* download-path class (one-line fix + test, worth doing in
the next batch rather than re-opening this five-gate loop), F2 is the CLI half
of the already-deferred surfacing concern, and F3/F4 are one-line cleanups.
Recommend recording F1–F4 in TODO.md alongside the deferred regate4 items so
none are silently dropped; do not treat this APPROVED as "the P5 class is
closed end-to-end" — say "closed for metadata/search APIs; download path still
bare (see F1)."
