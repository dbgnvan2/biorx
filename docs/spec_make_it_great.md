# Making biorx GREAT — Differentiation Spec

Status: proposal, revision 2 (review corrections applied). Date: 2026-09-16.
Audience: the coding agent + Dave.

Thesis: biorx does not win by out-S2-ing Semantic Scholar (more papers, more
citations). It wins by combining three things S2 structurally cannot offer —
(a) native per-source search precision, (b) identity resolution across the
preprint↔published divide, and (c) personalization (saved filters, monitoring,
per-user summaries, full-text search over *your own* library). This spec is
organized around the identity foundation, then those three pillars.

**Naming.** Phases are `G0`–`G6` and items `G1.2` etc. They are deliberately not
`P…`, which is the failure-pattern catalogue in `~/.claude/standards/learnings.md`.
References to that catalogue below are written "learnings P35".

---

## 0. Corrections and verified facts (read first)

Each item was checked against the code, or against a live API, on 2026-09-16.

Confirmed:

- **OpenAlex is not a search adapter.** `src/sources/config.py` lists it with
  `enabled: False` and `SOURCE_LABELS` names it, but there is no
  `src/sources/openalex.py`. OpenAlex *is* already called for one thing:
  `src/paper_meta.py:fetch_openalex_abstract` (abstract recovery, N2).
- **Dedup is hand-rolled** in `src/sources/dedup.py`. Identity precedence is
  normalized DOI → PMID → PMCID → title + first-author surname + year. The title
  key does no Unicode (NFKC) normalization. There is no arXiv-id index.
- **Ranking has no citation signal and no relevance signal.**
  `orchestrator._rank()` is trust weight + DOI + abstract + OA − retraction
  penalty. The source's own relevance order is not an input.
- **No citation graph, no related-papers, no "cited by".**
- **`psyarxiv.py` and `socarxiv.py` are near-identical** (diff: docstrings,
  class name, provider slug).

Corrections to revision 1:

- **`canonical_id` is not "`doi:`-or-nothing".** `make_canonical_id`
  (`src/sources/schema.py:153`) already falls back DOI → PMID → PMCID →
  title hash, and the arXiv adapter sets `arxiv:<id>`. Since N1 (`d278e8d`),
  `canonical_id` is the unique database identity of a paper. Revision 1's plan to
  replace it with an OpenAlex id "when available" is withdrawn — see G1.1.
- **Europe PMC is not searched "as abstracts only".** It is already searched
  across full text, unintentionally. The query builder sends the "both" field as
  bare terms (`query_builder.py:55`, whose comment claims bare terms match title
  or abstract). Live check, 2026-09-16:
  `ketamine AND "default mode network"` → **1,577** hits;
  `TITLE_ABS:(ketamine AND "default mode network")` → **54** hits.
  Revision 1's "add a full-text toggle" is inverted; the real work is G0.1.
- **The app is not per-user below the filter level.** `papers`, `bookmarks`,
  `reference_lists` and `reference_list_items` have no `user_id`.
  `summaries.paper_id` is `UNIQUE`: one summary per paper for everyone, replaced
  by a later run (`created_by_user_id` records whose run is current). "Private
  summaries" and "your own library" need G2 before they are true.
- **The retrieval layer is locked by a test.** `tests/web/test_no_retrieval_drift.py`
  fails on any change to the orchestrator, dedup, query builder, schema and
  adapters since baseline `a3769ef` (plan 2026-09-15, W1.a). Several items here
  change those files. See §4.
- **Ollama is not available to the deployed web app.** `llm_config.yaml` points
  Ollama at `localhost:11434`, which does not exist inside the Railway container.
- **Nothing stores extracted PDF text.** `pdf_handler.extract_text` runs on
  demand; there is no text table.

To verify before building on it (not checked):

- **OpenAlex access terms.** Revision 1 said "no key, generous limits". OpenAlex
  may since have moved to keyed access with a free daily allowance. Check the
  current docs before G1.3/G5.1.
- **Which preprint servers are still on OSF.** ChemRxiv, EarthArXiv and engrXiv
  may have left OSF. Use the live list at `https://api.osf.io/v2/preprint_providers/`.

---

## 1. What Semantic Scholar users complain about

**Evidence status: anecdotal.** Gathered from forum posts and reviews
(2024–2025); links were not recorded and the claims have not been re-verified.
Add a link to each row before this section is cited anywhere else.

| # | Complaint | Source (unlinked) | biorx's answer |
|---|---|---|---|
| C1 | Rate limits are a bottleneck — ~1 req/s with a key; projects hit 429s and add a second provider | GitHub issue, SakanaAI/AI-Scientist | Use S2/OpenAlex for sparse lookups only, never the search path |
| C2 | Weak coverage of social sciences vs Google Scholar | Reddit r/research | biorx already queries PsyArXiv/SocArXiv natively; add more OSF servers (G4.1) |
| C3 | Older, highly cited papers dominate results. (S2's default sort is relevance, not citation count; the complaint is that citations weigh heavily in it.) | blog post, Quora | Citations as one bounded signal, recency as another, never alone (G5.1) |
| C4 | Fewer results than Google Scholar / nothing relevant | Reddit r/academia | Native multi-source + `sources_failed` transparency; library search (G5.3) |
| C5 | UI complaints | Reddit r/research | Product decision, not code |
| C6 | Retrieval returns metadata, not full text | Reddit r/Rag | biorx downloads PDFs and extracts text locally (already does) |
| C7 | No saved-search monitoring or personalization | (absence) | Saved filters + monitoring with deltas (G3) |

The main observation stands regardless of the individual rows: **S2 has no
notion of "you".** biorx remembers what a user is looking for and can tell them
when something new appears.

---

## 2. The moat — do not regress these

1. **Native source queries** — Lucene with species clauses (Europe PMC/PubMed),
   date-windowed streams (bioRxiv/medRxiv).
2. **Source-trust ranking** with retraction penalty.
3. **Local summarization** (Ollama on the desktop; DeepSeek/Anthropic as paid
   providers, which is what the web app uses).
4. **PDF download + local full-text extraction.**
5. **Saved filters + monitoring** (`agents/monitor.py`, cron-driven).
6. **Transparency** — `sources_failed`, "empty ≠ quiet week", retraction flags.
7. **Stable paper identity** — a stored paper keeps its `canonical_id` (N1).

---

## 3. Prerequisite: the approved backlog

`docs/implementation_plan_2026-09-16_backlog.md` is approved and in progress
(N1, N2 done; batch H next). Batches C and D change the same adapters this spec
changes. **Finish backlog batches H, A, D and C before G1.** Batches B, E, F, G
and I are independent and can interleave.

---

## 4. The retrieval lock

Items marked **[unlocks W1.a]** change files protected by
`tests/web/test_no_retrieval_drift.py`. For each such item, the commit:

- updates `BASELINE` in that test to the new commit's parent, and
- says in the commit message which protected file changed and why.

The lock stays in place; it is re-baselined deliberately, one item at a time,
never removed.

---

## 5. The spec

### G0 — Search precision bug (small, do first)

**G0.1 Europe PMC "both" field searches full text by accident.** [unlocks W1.a]
Send "both" terms as `TITLE_ABS:` in `_group_to_lucene`, and fix the comment.
Offer full-text search as an explicit per-filter option (`search_full_text`,
default off) that sends bare terms, so the high-recall behaviour is kept for
users who want it.
- Existing saved filters change behaviour (fewer hits). Announce it in the
  changelog and README; do not migrate filters silently.
- Check the PubMed adapter, which shares `build_europepmc_query`, for the same
  field semantics.
- Acceptance: a query-builder test asserting `TITLE_ABS:` for the default and a
  bare term with `search_full_text`; a recorded live hit-count comparison noted
  in the gate file (integration-only, flagged as such).

### G1 — Identity foundation

**G1.1 Keep `canonical_id` stable; store other ids beside it.**
Add nullable columns `arxiv_id` and `openalex_id` to `papers` via
`_add_column_if_missing`, with indexes. `canonical_id` is set once when a paper
is first stored and never rewritten by enrichment. Lookups (summaries, lists)
may resolve through any stored id.
- Rule: identity must not depend on whether an external call succeeded
  (learnings P1). A failed OpenAlex lookup leaves the paper exactly as it was.
- Acceptance: `test_g1_1_canonical_id_unchanged_after_enrichment`;
  `test_g1_1_lookup_by_arxiv_id_finds_doi_keyed_paper`; a dirty-state test on a
  populated DB (learnings P8).

**G1.2 Dedup additions within one search.** [unlocks W1.a]
In `dedup.py`: add an arXiv-id index after PMCID (normalize `arXiv:2106.12345v2`
→ `2106.12345`), and NFKC-normalize `_norm_title`. **No ±1-year tolerance** — it
merges corrections, errata and conference abstracts with their articles.
- Acceptance: `test_g1_2_arxiv_prefix_and_version_dedup`;
  `test_g1_2_nfkc_title_match`; adversarial
  `test_g1_2_correction_notice_does_not_merge_with_article`.

**G1.3 Link preprints to published versions; do not merge them.** [unlocks W1.a]
A preprint and its published article stay separate records. Add a
`paper_links` table (`from_canonical_id`, `to_canonical_id`, `relation`
= `published_as`, `source`, `found_at`). The UI shows "Published as …" /
"Preprint version …" on each.
- Why not merge: `_merge` makes `is_preprint` sticky, so a merged article would
  be labelled a preprint; and the two versions can differ in content.
- Sources, cheapest and most deterministic first:
  1. bioRxiv/medRxiv API `published` field (DOI of the published version).
  2. Crossref `relation.is-preprint-of` / `has-preprint`.
  3. OpenAlex, only if 1–2 give nothing, and only after its terms are verified (§0).
- A failed lookup records nothing and is retryable; it is never stored as
  "no published version" (learnings P1).
- Acceptance: `test_g1_3_biorxiv_published_doi_creates_link` (real recorded
  bioRxiv response fixture); `test_g1_3_linked_versions_remain_two_rows`;
  `test_g1_3_lookup_failure_writes_no_link`.

### G2 — Per-user scoping (prerequisite for every "your library" feature)

**G2.1 Decide the sharing model.** Needs a decision from Dave before planning:
- (a) Papers stay a shared catalogue; bookmarks, reference lists and summaries
  become per-user. (Recommended — papers are public metadata.)
- (b) Everything per-user.
- Summaries under (a): per-user rows (`UNIQUE(paper_id, user_id)`), or shared
  summaries with a per-user "mine" view. Summaries cost money on the owner key,
  so sharing them saves spend; per-user matches the "private" claim.

**G2.2 Implement the chosen model.** Add `user_id` to `bookmarks`,
`reference_lists` (items inherit through `list_id`), and summaries per G2.1, via
additive migration. Existing rows (desktop, no user) get a fixed `local` user id.
- Fix in the same change: `reference_list_items` has `UNIQUE(list_id, doi)`,
  which allows unlimited duplicates of DOI-less papers (the N1 class). Key it on
  `canonical_id`.
- Acceptance: `test_g2_2_user_a_cannot_see_user_b_list` (web route);
  `test_g2_2_doi_less_paper_added_twice_is_one_item`; migration test on a
  populated DB with exact before/after row counts.

### G3 — Monitoring (the differentiator; attacks C7)

**G3.1 New-since-last-run.** Store, per filter, the set of `canonical_id`s its
last run produced, plus that run's `complete` flag (true only if no source
failed) and the id-scheme version.
- If the previous run was incomplete, or this run is incomplete, report results
  as "new since last complete run" against the last complete baseline, and say
  so. Never report a paper as new only because the baseline could not be read,
  and never treat a paper missing from an incomplete run as removed
  (learnings P35, P31).
- If the id-scheme version changed (any G1 change to how ids are derived), the
  first run after it re-baselines and reports "baseline reset", not N new papers.
- A preprint whose published version appears is reported as "now published",
  not as a new paper (uses G1.3).
- Acceptance: `test_g3_1_failed_source_does_not_inflate_new_count`;
  `test_g3_1_first_run_after_id_change_reports_reset`;
  `test_g3_1_second_run_reports_only_additions` (dirty state).

**G3.2 Scheduled searches in the web app.** Per-user schedules persisted in a
`filter_schedules` table (`filter_id`, `cadence`, `last_run_at`, `next_run_at`).
An in-process scheduler reads that table at startup, so a redeploy delays a run
but does not lose it; a run missed during downtime runs once on startup.
- Results appear in an in-app "new since last run" feed. There is no email:
  there are no accounts (§6), so a user sees alerts only when they visit.
- Assumes one container. If Railway is ever scaled to more than one replica,
  runs duplicate; a row-level claim (`UPDATE … WHERE next_run_at = ?`) guards it.
- Scheduled runs never call a paid LLM automatically.
- Acceptance: `test_g3_2_schedule_survives_restart`;
  `test_g3_2_missed_run_executes_once`; `test_g3_2_scheduled_run_spends_nothing`.

**G3.3 Summarize the new arrivals.** One click on the feed summarizes the N new
papers through the existing providers and spend cap. Depends on G2 for whose
summaries they are.
- Acceptance: `test_g3_3_batch_summary_respects_owner_cap` (the (N+1)th is
  refused, not billed).

**G3.4 Citation export.** BibTeX, RIS and CSL-JSON from a reference list.
Depends on G2.2.
- Papers without a DOI export with their other ids (arXiv, PMID) or URL; none is
  silently skipped. Report "exported N of M" if any cannot be written.
- Acceptance: round-trip test parsing each exported format back and comparing
  entry count to list size (learnings P19); `test_g3_4_doi_less_paper_exports`.

### G4 — Coverage (attacks C2)

**G4.1 One OSF adapter, parameterized by provider.** [unlocks W1.a]
Replace `psyarxiv.py` and `socarxiv.py` with `osf.py` taking a provider slug;
keep the `psyarxiv` / `socarxiv` source names so saved filters still work.
Enable additional servers from the verified OSF provider list (§0), one config
entry each.
- OSF has no server-side text search: adapters page by date and filter client
  side. Each extra server multiplies pages fetched. Log "fetched N pages, kept M
  of K records" per server, and apply the existing page cap per server with the
  drop announced (learnings P9).
- Acceptance: the existing PsyArXiv/SocArXiv adapter tests pass unchanged against
  `osf.py`; `test_g4_1_saved_filter_with_psyarxiv_still_runs`.

### G5 — Relevance & discovery (attacks C3, C4, C6)

**G5.1 Citation count and recency in ranking.** [unlocks W1.a]
Define the score before building it:
- Relevance input: each record's position in its source's own result order
  (normalized per source), since `_rank()` currently has none.
- Citations: `log1p(cited_by_count)`, capped in weight. A record whose lookup
  failed has citations = unknown and receives the neutral value, not zero
  (learnings P1/P35).
- Recency: exponential decay by age, with half-life in config.
- All weights in `sources_config.yaml`, not code. Retraction penalty stays larger
  than any positive signal.
- Citation lookups are batched (OpenAlex filter with piped DOIs, up to its
  per-request limit), cached via `src/sources/cache.py`, and time-boxed; ranking
  proceeds without them if they fail.
- Acceptance: adversarial `test_g5_1_old_highly_cited_irrelevant_paper_ranks_below_recent_relevant`;
  `test_g5_1_failed_lookup_is_neutral_not_zero`;
  `test_g5_1_retracted_paper_ranks_last_regardless_of_citations`;
  `test_g5_1_weights_come_from_config`.

**G5.2 "Cited by" and "Related".** Lazy, per-paper lookups when a paper is
opened (OpenAlex `cited_by` / `related_works`, or S2 recommendations), cached.
No crawling. A failed lookup shows "couldn't load", never "no citations".
- Acceptance: `test_g5_2_lookup_failure_is_distinct_from_empty`.

**G5.3 Search within the saved library.** Two steps:
1. Persist extracted text: a `paper_text` table (`paper_id`, `text`,
   `extracted_at`, `pages`), written when a PDF is extracted. Index it and
   abstracts with SQLite FTS5 for keyword search. Works in both desktop and web.
2. Semantic search with embeddings — **desktop only** unless an embedding
   provider is added to the web deployment. On the desktop, `nomic-embed-text`
   via Ollama, vectors in SQLite, brute-force cosine. No vector database.
- Scoped to the user's library as defined by G2.
- Acceptance: `test_g5_3_extracted_text_is_persisted`;
  `test_g5_3_fts_finds_term_only_in_full_text`; step 2 flagged integration-only.

### G6 — Hardening

**G6.1 Backups.** A nightly job copies `biorxiv.db` to object storage (R2/S3)
using SQLite's backup API or `VACUUM INTO` — never a raw file copy of a live
database. Optionally `PDFs/`. Write restore instructions and test a restore.
- The database holds users' encrypted LLM keys. The encryption key must not be
  stored in the backup bucket or its credentials.
- Retention and failure alerting in config; a failed backup is logged as an
  error and visible on a health/status route.
- Acceptance: `test_g6_1_backup_of_db_under_write_is_consistent`
  (open the copy, `PRAGMA integrity_check`, row counts match);
  `test_g6_1_backup_contains_no_encryption_key`; restore drill recorded
  (human review).

---

## 6. What is deliberately *not* in this spec

- **Full S2 corpus replacement.** biorx needs the right papers for this user,
  not 214M papers.
- **Accounts/SSO/permissions.** Out of scope (README). A shared access code plus
  opaque user ids fits a few colleagues. Consequence: no email alerts (G3.2).
- **A vector database.** G5.3 ships on SQLite.
- **Merging preprints with published versions.** They are linked (G1.3).
- **Replacing `canonical_id`** with an external id (G1.1).
- **Becoming Google Scholar.** biorx's value is known sources, reproducible
  queries, and honest "I found nothing."

---

## 7. Build order

| # | Item | Why here |
|---|---|---|
| 1 | Backlog H, A, D, C | Approved and in progress; D/C touch the same adapters (§3) |
| 2 | G0.1 | A live precision bug in every Europe PMC/PubMed search |
| 3 | G1.1, G1.2, G1.3 | Identity must be settled before deltas depend on it |
| 4 | G2.1 decision, G2.2 | Every "your library" feature depends on it |
| 5 | G3.1, then G3.2 | The main differentiator; G3.1 needs G1 settled |
| 6 | G6.1 | Before other users rely on scheduled data |
| 7 | G5.1 | Visible improvement; needs OpenAlex terms verified |
| 8 | G3.3, G3.4, G4.1, G5.2, G5.3 | As time allows |

## 8. Decisions needed from Dave

1. G2.1 — sharing model (a or b), and per-user vs shared summaries.
2. G0.1 — confirm default "both" = title/abstract, with full text as an option.
3. G3.2 — confirm no automatic paid summarization on scheduled runs.
