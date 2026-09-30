# Implementation plan — biorx next steps (post-review, 2026-09-29)

**Source of findings:** `REVIEW-biorx-2026-09-28.md` (fully fixed across batches 1–8),
the browser run + production check (`docs/cycles/2026-09-29_browser-run.md`), and a fresh
reconciliation of `TODO.md`'s deferred tail against current code.
**Status:** Closed 2026-09-30 — every item done, not needed, or deferred by the owner (see the table).

| Item | Status | Proof |
|---|---|---|
| Stored " (PubMed)" labels | done | `tests/test_db_migrations.py::test_br14_stored_pubmed_label_fixed` |
| T1.1 | done (particles kept: "Ana da Silva" = "da Silva", "Ana Silva" ≠ "da Silva" — QA gate F1) | `tests/test_dedup.py::test_t11_*` |
| T1.2 | not changed — `reference_list_items` is only used by `gui.py` (D1); the web app's `user_reference_list_items` is `UNIQUE(list_id, paper_id)` | `src/db.py` (table), `src/user_store.py` |
| T1.3 | done | `tests/test_fulltext.py::test_t13_openalex_refusal_reads_as_temporary` |
| T1.4 | done | `tests/web/test_frontend_wiring.py::test_f1_find_by_title_falls_back_to_the_server_default`, `tests/web/test_summaries_routes.py::test_t14_*` |
| T1.5 a/b/c | done | `tests/web/test_auth.py::test_t15a_*`, `::test_t15c_*`, `tests/web/test_access_codes.py::test_t15b_*` |
| T1.6 | done | `tests/test_paper_meta.py::test_t16_landing_page_is_not_tried_as_the_pdf` |
| T2.1 | done (D2 decided 2026-09-30: remove) | `tests/web/test_auth.py::test_d2_old_name_sign_in_is_gone`, `tests/web/test_frontend_wiring.py::test_pc13_sign_in_page_is_code_then_pin` |
| T2.2 | done | `tests/test_tokens.py::test_t22_*` |
| T2.3 | done | `tests/test_h_environment.py::test_t23_*` |
| T2.4 | already fixed (list, both run, warned); now tested | `tests/test_monitor.py::test_t24_*` |
| T2.5 | not changed — goes with D3, skipped for now | — |
| T2.6 | not needed — a full-text summary is stored and reused (`src/summarize.py`), so its PDF is never fetched twice; a re-fetch only follows a try that found no PDF | — |
| T2.7 | done | `tests/web/test_jobs.py::test_t27_*`, `tests/web/test_frontend_wiring.py::test_t27_*` |
| T3.1 | done | `src/paper_meta.py` `_OUR_BUGS` comment |
| T3.2 | done | `tests/web/test_deploy_files.py::test_the_entrypoint_refuses_to_start_on_an_unwritable_volume` |
| T3.3 | done (found `BIORX_PDF_FONT` undocumented) | `tests/web/test_deploy_files.py::test_t33_*` |
| T3.4 | done | `tests/test_paper_meta.py::test_t34_*` |
| T3.5 | done (widened to `docs/` except `docs/cycles/`) | `tests/test_h_environment.py::test_t35_*` |
| T3.6 | done (note corrected: extra hits are filtered out) | `src/sources/query_builder.py` `_group_to_arxiv` |
| T3.7 | skipped for now (D3) | — |
| T3.8 | done (ignored, not deleted) | `tests/web/test_deploy_files.py::test_t38_*` |
| D1 | decided 2026-09-30: retire `gui.py` — done | `tests/web/test_deploy_files.py::test_d1_desktop_app_is_retired` |
| D2 | decided 2026-09-30: remove the old name sign-in — done (T2.1) | as T2.1 |
| D0 | decided 2026-09-30: accept the ~3-minute bioRxiv/medRxiv searches for now (option A); a cache (option C) only if it gets in the way | `TODO.md` (browser run section) |
| D3 | decided 2026-09-30: skip spend visibility for now (with T2.5, T3.7) | — |

Original status: PLAN — no code written.
**Surface:** web app, shared `src/`, `agents/monitor.py`. `gui.py` is still not changed
(retiring).

---

## 0. Current state (verified, not assumed)

- **Suite green.** `venv/bin/python -m pytest tests/ -q` → **1625 passed, 2 skipped** (75s).
- **Every 09-28 review finding is closed.** `docs/spec_coverage_review_fixes.md` lists
  B1–B10, A1–A14, S1–S4, M1–M39, T1–T2 all `done` with a named test each.
- **Browser + production pass done** (09-29): 14 common flows + a Railway production check;
  4+ defects found and fixed with tests (local model empty response; bioRxiv/medRxiv read
  ~1% silently; a refused key kept in the browser; HTML tags in abstracts; plus budget
  accounting, table scroll, http-redirect handling, refusal notes).
- **Most of TODO.md's "deferred" tail is now moot** — verified fixed in current code:
  `datetime.utcnow()` (gone), arXiv version carried (`src/sources/arxiv.py:311-359`),
  CSP header (`web/app.py:95`), static cache-busting (`web/app.py:195-203` +
  `versioned_index`), arXiv wildcards stripped (`query_builder.py:283`), contact addresses
  env-driven (`src/sources/config.py:32`, `BIORX_CONTACT_EMAIL`), summarization shared
  pipeline (`src/summarize.py`), cross-source surname key (`dedup.py:49-67`).

What remains is a short, mostly low-severity tail plus **one real product decision**.

---

## 1. The one decision that blocks the rest

### D0 — bioRxiv/medRxiv search takes ~3 minutes per run

Their API cannot search words, so the adapter reads every paper in the date window
(≈4,900 papers for two weeks ≈ 3 min; 5,095 on production) and filters locally, bounded
only by `publication_sources.biorxiv_medrxiv.max_pages` (currently 150) in
`sources_config.yaml`.

Options (pick one; this changes what the other tasks look like):

| Option | Cost | Effect |
|---|---|---|
| **A. Accept** 3 min, keep `max_pages` high | 0 | Every paper in window is read; result is complete |
| **B. Lower `max_pages`** | 1-line config | Faster, but silently truncates (currently *reported* as `page-limit`, so not silent — just less complete) |
| **C. Cache the window per (date-range, source)** | a few hours | Read once, reuse across searches; invalidation + staleness questions |
| **D. Query bioRxiv's real search endpoint** for the title terms | unknown | Their search is weak (title-only, no abstract), so recall drops — the reason the current design reads everything |

**Recommendation: A + a cache later (C) if latency actually hurts.** The 3-minute cost is
the honest price of "search bioRxiv/medRxiv properly"; truncating or degrading recall is the
same class of silent-narrowing the whole review just spent 8 batches removing (P13).

*Other questions carried from the review, still undecided (answer to close the switch-over):*
- **D1 — is `gui.py` (2,536 lines) actually retired?** It imports shared modules whose
  interfaces changed (`filtering.py`, `db.py`, `query_builder.py`, adapters). Either delete
  it + its two test files, or state it is supported and route it through the same code as
  the web app. Leaving it half-maintained is the only option that keeps accruing debt.
- **D2 — is `ACCESS_CODE` fully removed everywhere?** If so, delete the old name sign-in,
  `POST /api/session/recover`, the recovery-code dialog, and the `accounts.sign_in` /
  `create_account` / `recover` paths (TODO 2026-09-18 "end of the switch-over").
- **D3 — should per-user spend be visible in the web app?** Needs the usage plan approved
  (it is not). If yes, this is a small feature, not a bug fix.

---

## 2. Work items (prioritized)

### Tier 1 — small correctness/data fixes (each a self-contained commit + test)

**T1.1 · M22 residual — multi-word surname still doesn't merge cross-source.**
`src/sources/dedup.py:49-67` `_surname`: Europe PMC `family="da Silva"` →
`re.sub(r"[^\w]", "", "da silva")` → `dasilva`; arXiv `"Ana da Silva"` → last word →
`silva`. Same paper, two keys, never merges by title. Fix: strip a leading lower-case
particle from the *last-word* branch only (or split on `family` case-insensitively at the
space). Test: Europe PMC "Ana da Silva" vs arXiv "Ana da Silva" merge; a different "Silva"
paper does not.

**T1.2 · N1-adjacent — `reference_list_items` dedups on `(list_id, doi)`, NULLs distinct.**
`src/db.py:334` `UNIQUE(list_id, doi)`. A DOI-less paper can be added to the same list
twice. Key on `canonical_id` (already the papers identity, `db.py:743-765`). Test: add a
DOI-less paper twice → one row.

**T1.3 · G1 — `fulltext.default_get_json` reports any 401/403 as a settings problem.**
Only Unpaywall's 401/403 means "no contact email". An OpenAlex / Semantic Scholar 403
(quota) reads as temporary. Split the message by which finder failed. Test: OpenAlex 403 →
"try again" wording, Unpaywall 403 → "settings" wording.

**T1.4 · G2 — `/api/config` failure at load overrides an operator's `find_by_title: false`.**
`web/static/app.js:624-650` falls back to the default when the config read fails. If the
operator's own choice was already persisted locally, the fallback must not silently flip it.
Test: saved `find_by_title:false` + failing config fetch → still false.

**T1.5 · M4 residuals — three small hardening gaps around sign-in:**
(a) the rate limiter spends its token before the PIN-check slot is taken; (b) `reset_pin`
is not one transaction; (c) `from_config` does not guard `int()` on the `sign_in:` values.
Each is a small, contained fix with its own test in `tests/web/test_auth.py` /
`test_access_codes.py`.

**T1.6 · N2-adjacent — `pdf_url()` falls back to `best_oa_url` (an HTML page) for PMC.**
`src/fulltext.py` (finder order). The summary job downloads a web page as if it were a PDF
before recovery runs. Use `url_for_pdf` / Unpaywall `url_for_pdf` first and skip the HTML
fallback for PMC. Test: PMC record with `best_oa_url` → no HTML-as-PDF attempt.

### Tier 2 — cleanup / hardening (a few commits)

**T2.1 · End of switch-over (blocked on D2).** Delete old name sign-in, recover route,
recovery dialog, and unused `accounts` paths once `ACCESS_CODE` is gone everywhere.
Test: the routes return 404; no import remains (`tests/web/test_deploy_files.py` pattern).

**T2.2 · OllamaClient hardcoded defaults.** `src/llm.py:17` `OLLAMA_MODEL = "qwen3.5:4b"`
and `:24` `timeout: int = 120` are only reachable via a bare `OllamaClient()`; the real
path passes config (`llm_providers.py:431-433`). Remove the constants (or make them
required params) so no stale model id or timeout lingers in source. Test: `grep`-based
guard like the existing vocabulary no-literals check.

**T2.3 · Bare `"biorx/1.0"` UA without a mailto.** `src/sources/unpaywall.py:31` and
`src/pdf_handler.py:94` don't use `polite_user_agent`, so their polite-pool requests carry
no contact address. Route both through the same helper. Test: the UA string contains the
contact email when set.

**T2.4 · `filters.json` name uniqueness for `monitor.py`.** `monitor.load_filters()` keys
by name, so two filters called "New Filter" → `--all` silently drops one. Either refuse a
duplicate name on save (mirror the web app's 409) or key by id. Test: two same-name
filters → both run (or the save is refused).

**T2.5 · Discover shares the summary cap (`kind="summary"`).** Deliberate today, but it
means the usage log mislabels a discover run. If discover gets its own allowance, add a
`kind` and a cap query in the same change (otherwise it silently leaves the cap's count).
Test: discover usage recorded under its own kind.

**T2.6 · Summaries don't reuse the PDF cache.** The cache-poisoning fix re-downloads each
PDF per summary. A cache keyed by a hash of the server-validated URL restores reuse without
the poisoning risk. Test: two summaries of one paper → one fetch.

**T2.7 · Unbounded job creation (memory).** The spend cap bounds money, not memory; any
signed-in user can queue jobs without limit. A per-user queue cap (mirroring the one-search
per-user limit) closes it. Test: a user's Nth queued job is refused.

### Tier 3 — docs / diagnostics / low-severity (batch them in one PR)

- **T3.1** `_OUR_BUGS` is broader than its name (`ImportError` can mean a missing optional
  dep) — fix the docstring (`src/paper_meta.py` / wherever it lives now).
- **T3.2** `docker-entrypoint.sh` `fatal()` second line always says "mount writable by
  biorx" even when the platform forced a different uid — name the real uid (chunk-5 F8).
- **T3.3** The "derive env vars from the code" test misses indirect reads — extend it.
- **T3.4** `paper_meta` recovery paths emit no warning when no contact email is found
  (batch-H P5) — add `get_contact_email()` + `log.warning` in the standalone callers.
- **T3.5** `_root_md_files` scans root `.md` only — document that `docs/` is excluded
  deliberately (batch-H F4), or widen to non-cycles content.
- **T3.6** `all:` matches more than Europe PMC's bare term (authors, comments, journal-ref)
  — document it in the filter vocabulary (code review 09-15).
- **T3.7** Per-user spend visibility — only if D3 is approved (new feature, not a bug).
- **T3.8** Housekeeping: remove the stale untracked `.test-qa-report.md` (09-17 artifact)
  and decide whether `.claude/` launch configs belong in git or `.gitignore`.

---

## 3. Sequencing

1. **Answer D0** (and D1–D3) first — D1 and D2 gate whole task groups.
2. **Tier 1** (T1.1–T1.6) — six small correctness fixes, each: write the failing test,
   fix, run the suite, commit. All independent; can run in parallel.
3. **Tier 2** (T2.1–T2.7) — T2.1 blocked on D2; the rest independent.
4. **Tier 3** — one cleanup PR.

Each task through the existing loop: test-first → `venv/bin/python -m pytest tests/ -q`
→ commit. The browser-run and production-check steps that caught the last round's defects
should be re-run for anything touching `app.js`, retrieval, or summaries (the LEARNINGS
common-flow check: empty filter, no-match filter, match filter).

---

## 4. Tests / verification

- Full suite: `venv/bin/python -m pytest tests/ -q` (currently 1625 passed, 2 skipped).
- Any retrieval change: re-run the live saved-filter check (`monitor.py --dry-run --json`)
  and diff against `docs/cycles/2026-09-28_retrieval_baseline.json`.
- Any `app.js` change: node-harness test + a browser smoke run (as in
  `docs/cycles/2026-09-29_browser-run.md`).
- Any `summarize.py` / `db.py` change: confirm no summary downgrade path reopened (the
  A1/M26 tests are the canaries).

---

## 5. Risks / open questions

- **bioRxiv/medRxiv latency (D0)** is the only user-visible friction left; everything else
  is correctness-at-the-margins or hygiene.
- **GUI (D1)** is the largest single source of unaddressed debt; it imports interfaces that
  have since changed and is untested in CI. Deleting it removes a class of findings.
- **Scope creep risk:** Tier 3 items are individually trivial; batch them so they don't
  each become their own review cycle.
