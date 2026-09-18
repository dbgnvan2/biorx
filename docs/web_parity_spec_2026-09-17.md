# Web App Feature Parity Spec
**Date:** 2026-09-17  
**Branch:** main (post `f031d12`)  
**Goal:** Extend the BioRx web app to full functional parity with the PyQt6 desktop GUI so it is shareable via URL without requiring install.

---

## Background

The desktop GUI has four tabs. The web app currently has: a Search tab and an LLM settings panel only. This spec covers everything needed to close that gap.

Desktop tabs: **Search & Browse**, **Filters**, **Saved References**, **Settings**.

---

## Scope

### In scope (this spec)
- FP1 — Filters tab (full CRUD + test run + AI term discovery)
- FP2 — References tab (per-user lists, PDF download, CSV/Excel export)
- FP3 — Settings tab (per-user settings; see PS1–PS9 plan — server config editing removed)
- FP4 — Search enhancements (source picker, category, date range, checkboxes, save as reference list, paper detail modal)

### Out of scope (explicit)
- Auto-run scheduled searches (cron / background daemon)
- Batch PDF ingestion at startup
- Desktop-specific features: OS notifications, local Finder reveal

---

## Data model additions (src/db.py)

### D1 — user_reference_lists table

```sql
CREATE TABLE IF NOT EXISTS user_reference_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(user_id, name)
);
```

Migration added via `_add_column_if_missing` pattern — but since this is a new table, use `_create_table_if_missing` (add that helper if absent, else inline the CREATE IF NOT EXISTS).

### D2 — user_reference_list_items table

```sql
CREATE TABLE IF NOT EXISTS user_reference_list_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id     INTEGER NOT NULL REFERENCES user_reference_lists(id) ON DELETE CASCADE,
    paper_id    INTEGER NOT NULL REFERENCES papers(id),
    added_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(list_id, paper_id)
);
```

Both tables added in `Database._run_migrations()`, executed on every startup.

---

## Backend additions

### FP3-B — Settings routes — REMOVED

Superseded by `docs/implementation_plan_2026-09-17_per_user_settings.md` (PS1–PS9).
The first build let any signed-in user read and overwrite the server's
`sources_config.yaml` and `llm_config.yaml`, which changed the app for every user.
The web app has no route to those files; they are owner-only, edited on the server
or via environment variables.

---

### FP2-B — References routes (web/routes_references.py)

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/api/references` | List user's reference lists (id, name, item_count, created_at). |
| POST   | `/api/references` | Create list `{name: str, max_length=200}`. Returns created list row. 201; 409 if the user already has that name. |
| DELETE | `/api/references/{id}` | Delete list and its items (deleted explicitly — SQLite foreign keys are off, so CASCADE does not fire). 404 if not owned by user. |
| GET    | `/api/references/{id}/items` | Papers in list (full paper dict). |
| ~~POST~~ | ~~`/api/references/{id}/items`~~ | **Removed 2026-09-17** (security review): it stored a client-supplied paper and URL for the proxy to fetch. Papers enter lists via save-as-list only. |
| DELETE | `/api/references/{id}/items/{item_id}` | Remove one item. |
| GET    | `/api/references/{id}/export.csv` | CSV download: title, authors, date, doi, source, pdf_url. |
| GET    | `/api/references/{id}/pdf/{paper_id}` | Proxy: fetch PDF URL for that paper, stream bytes back as `application/pdf`. |

**Authorization:** every route checks `user_id` ownership of the list before acting.

**PDF proxy security (SSRF + size)** — as built in `src/safe_fetch.py` after the 2026-09-17 security review (the first version checked only the first URL and followed redirects):
- https only, on every redirect hop; redirects followed by hand, at most 5.
- The host must resolve (fail closed) and every address must be globally routable — excludes loopback, RFC 1918, link-local/cloud metadata, CGNAT, 0/8, IPv6 ULA/link-local, IPv4-mapped forms.
- The connection is pinned to a checked address, TLS verified against the hostname, so DNS rebinding has no second lookup to use.
- Body streamed and abandoned past `PDF_MAX_BYTES` (100 MB); must start with `%PDF-` (a landing page is 422, not served as a PDF).
- Verify `paper_id` is an item in `list_id` before fetching — ownership of the list is not sufficient.
- Outcomes: 403 refused, 422 not a PDF, 413 too large, 502 upstream failure.

---

### FP1-B — Filter test + term discovery (additions to web/routes_filters.py + new routes_discover.py)

| Method | Path | Description |
|--------|------|-------------|
| POST   | `/api/filters/{id}/test` | Run filter as a search job; returns job_id. Uses existing job machinery. |
| GET    | `/api/searches/{job_id}/results` | Already exists — reused for filter test results. |
| POST   | `/api/discover-terms` | Body: `{description, sources?, category?}`. Runs a broad search, feeds titles+abstracts to LLM. 202 + job. |
| GET    | `/api/discover-terms/{job_id}` | Poll; includes `result` once done. (The search poll payload never carries results.) |

**Filter test:** `POST /api/filters/{id}/test` deserializes the stored `filter` JSON, builds an `OrchestratorSearchParams` from it, submits a search job (same path as `/api/searches`), and returns `{"job_id": "..."}`. The frontend polls via the existing `/api/searches/{job_id}` + `/api/searches/{job_id}/results` endpoints.

**Discover terms:** caps input at 30 paper titles+abstracts; LLM prompt asks for 5–10 keyword phrases. Submitted as a **background job** (returns 202 + job_id; frontend polls `GET /api/discover-terms/{job_id}` until done and reads `result.terms`). The description is split into keywords (stop words removed, `src/discover.py`) and searched as a `both` text group; `days_back`, `max_papers` and the stop words are in `llm_config.yaml` under `discover:`. An owner-key slot is released if the model is never called. An unparsable reply is a job error, not an empty list. Respects the user's inline key / stored key / owner key with the same `_resolve_for` logic from routes_summaries.

**Discover-terms job result shape:** `{"terms": [...], "papers_found": N, "keywords": "..."}`; `sources_failed` on the job.

---

### FP4-B — Search route enhancements (web/routes_searches.py)

The existing `SearchParams` already accepts `category`, `date_from`, `date_to`, `sources`. Verify these are already wired through to the orchestrator (read the existing code). If any are missing, add them.

**New:** `POST /api/searches/{job_id}/save-as-list` — body `{name, paper_ids?}`. Saves the job's results (or a subset by `paper_ids`) as a new reference list for the current user. Returns the created list id.

---

### App registration (web/app.py)

Add imports and `application.include_router(...)` for:
- `routes_references.router`
- `routes_settings.router`
- `routes_discover.router` (discover-terms endpoint; returns 202 + job_id)
- Filter test endpoint (add to `routes_filters.router`)

Also update the `/healthz` handler to include `"sources"` list (see FP-UI-0).

---

## Frontend additions (web/static/)

### FP-UI-0 — Source list endpoint

`GET /healthz` currently returns: `ok, access_code_set, byo_keys_enabled, provider, model, owner_key_set, db_path, startup_warnings`. It does **not** return a source list.

Add to the healthz response: `"sources": [{"id": "europepmc", "label": "Europe PMC", "enabled": true}, ...]` — derived from `ctx.get_orchestrator().get_enabled_sources()`. The frontend source picker (Search tab and Filters tab) reads this list on boot.

---

### FP-UI-0.5 — Required HTML element IDs (spec-level enumeration)

These IDs must be present in `index.html` and referenced in `app.js`. The `test_every_element_the_client_uses_exists_in_the_page` and `test_the_page_has_no_orphaned_controls` tests enforce this exact set (modulo `LAYOUT_ONLY`).

**New tab navigation:**
- `tab-search`, `tab-filters`, `tab-references`, `tab-settings`

**Filters tab:**
- `filter-list`, `filter-name`, `filter-enabled`, `filter-category`, `filter-days`, `filter-date-range-toggle`, `filter-start-date`, `filter-end-date`, `filter-text-groups`, `filter-authors`, `filter-institution`, `filter-paper-type`, `filter-version`, `filter-published`, `filter-license`, `filter-species`
- Action buttons: `btn-filter-new`, `btn-filter-save`, `btn-filter-saveas`, `btn-filter-delete`, `btn-filter-test`
- Test results: `filter-test-results`
- Discover: `discover-desc`, `btn-discover`, `discover-terms-chips`

**References tab:**
- `ref-lists`, `ref-list-title`, `ref-papers`, `ref-checked-count`
- Action buttons: `btn-ref-new-list`, `btn-ref-delete-list`, `btn-ref-remove-selected`, `btn-ref-dl-selected`, `btn-ref-dl-all`, `btn-ref-export-csv`
- Progress: `ref-dl-status`

**Settings tab:**
- `settings-file-select`, `settings-editor`, `settings-status`
- Action buttons: `btn-settings-reload`, `btn-settings-save`

**Search tab additions:**
- `search-sources`, `search-category`, `search-days`, `search-date-range-toggle`, `search-start-date`, `search-end-date`
- `btn-save-as-list`, `save-list-name-input`
- `paper-modal`, `modal-title`, `modal-abstract`, `modal-summary`, `modal-pdf-link`, `btn-modal-close`

---

### FP-UI-1 — Tabbed layout

Replace the current single-pane layout in `index.html` with a four-tab shell:
- **Search** (existing content, plus FP4 enhancements)
- **Filters** (new)
- **References** (new)
- **Settings** (new)

Tab switching is pure CSS + JS (no page reload). Active tab stored in `localStorage` and restored on page load.

---

### FP4-UI — Search tab enhancements

1. **Source picker:** checkboxes for each enabled source (list from `/healthz`). Default: all checked.
2. **Category dropdown:** populated from a static list matching `BIORXIV_CATEGORIES` in gui.py (28 entries, starting with `"(all categories)"`).
3. **Date-range toggle:** "Use date range" checkbox. When off, shows "Days back" spinner (1–3650). When on, shows From/To date pickers.
4. **Paper checkboxes:** each result row gets a checkbox. "Select All / Clear" bar above table.
5. **Save as Reference List:** button appears when ≥1 paper selected. Prompts for list name via inline text input. Calls `POST /api/searches/{job_id}/save-as-list`.
6. **Paper detail modal:** clicking a title opens a modal with: full abstract, key findings (if summary exists), Open PDF link. Fetches summary from `/api/summaries/lookup` on open.

---

### FP1-UI — Filters tab

Two-column layout matching desktop: left list panel (filter names) + right editor panel.

**Left panel:**
- List of filters (name + enabled badge)
- New / Delete buttons

**Right panel (editor):**
- Name field (text input)
- Enabled checkbox
- **Scope group:** Category dropdown, days-back spinner OR date pickers (toggled), source picker checkboxes
- **Text search groups:** each group has multiple keyword rows (AND within group, OR between groups). Each row: field selector (title / abstract / authors), operator (contains / not contains), value. Add Group / Remove Group buttons. Within a group: Add Row / Remove Row.
- **Author / Institution:** comma-separated text inputs
- **Paper type / Version / Published / License / Species:** dropdowns (values from desktop constants)
- **Action bar:** New | Save | Save As | Delete | Test
- **Test results panel:** appears after Test; shows same paper table as Search tab; polling same job endpoints

**Discover Terms with AI section** (top of right panel, collapses to a one-liner when closed):
- Description textarea
- "Discover" button → calls `POST /api/discover-terms`, receives `{job_id}`; polls `/api/searches/{job_id}` until done; reads `result.terms`; renders returned terms as clickable chips that insert into text search groups

---

### FP2-UI — References tab

Two-column layout:

**Left panel:**
- List of reference lists (name + count)
- New List (name prompt) / Delete List buttons

**Right panel:**
- Selected list title
- Paper table: columns Title, Authors, Date, Source (checkboxes per row)
- Select All / Clear selection bar + "Remove Selected from List" button
- Download bar: "Download Selected PDFs" | "Download All PDFs" | "Export CSV"
  - PDF download: calls `/api/references/{id}/pdf/{paper_id}` and triggers browser download
  - CSV export: calls `/api/references/{id}/export.csv` and triggers browser download
- Progress indicator during batch PDF download

---

### FP3-UI — Settings tab (per user)

Per-user settings only (PS5):
- LLM settings panel (provider, API key, model), moved from the header.
- Default sources: which sources start ticked in the Search tab and in new
  filters. Stored in the browser's localStorage; limited to sources the server
  enables.

---

## Security considerations

- Settings: the web app never reads or writes server config files (PS1–PS3).
- PDF proxy: only fetch URLs that are in the `papers.pdf_url` column (resolved server-side from the paper row by `paper_id`), never accept a URL directly from the client.
- CSV export: values containing commas are quoted; values containing `=` or `+` at the start are prefixed with `'` to prevent formula injection in Excel (P-equivalent: untrusted data in CSV).
- Discover terms: cap LLM input; do not pass user-provided `description` unsanitized as a system prompt — wrap it in a user turn with a fixed system prompt.
- Paper detail modal and all new JS rendering: use `textContent` / `createTextNode` throughout. Never assign external API data (abstracts, author names, key findings) via `innerHTML`. The `test_the_client_never_builds_markup_from_paper_data` test in test_frontend_wiring.py must remain green.

---

## Test coverage required

| Area | Required tests |
|------|----------------|
| D1, D2 | Tables created by migration; CASCADE delete on list delete |
| FP3-B | Replaced by PS1–PS3: no settings route; config files byte-identical after a PUT attempt |
| FP2-B | CRUD round-trip; ownership check (other user's list → 404); CSV headers correct; PDF proxy 502 on bad URL |
| FP1-B | Filter test returns a job_id; job completes with results |
| FP4-B | save-as-list creates a reference list with correct items |
| FP-UI wiring | `test_every_element_the_client_uses_exists_in_the_page` and `test_the_client_calls_the_endpoints_that_matter` updated for all new ids and routes |

---

## Acceptance criteria (summary)

| ID | Criterion |
|----|-----------|
| FP1-A | User can create, edit, enable/disable, delete, and test a filter in the web UI |
| FP1-B | "Discover Terms with AI" generates keyword suggestions from a free-text description |
| FP2-A | User can create named reference lists; add and remove papers |
| FP2-B | PDF download and CSV export work from the References tab |
| FP3-A | Superseded: Settings tab holds per-user settings only (PS5, PS6) |
| FP3-B | Superseded: no server config editing from the web (PS1–PS3) |
| FP4-A | Search tab has source picker, category, date-range toggle, paper checkboxes |
| FP4-B | Selected papers can be saved as a named reference list |
| FP4-C | Clicking a paper title opens a detail modal with abstract and summary (if available) |
| SEC-1 | Superseded: settings API removed; `/api/settings/*` → 404 (PS1) |
| SEC-2 | PDF proxy fetches only the paper row's URL, and only under the `src/safe_fetch.py` rules (the row alone is not proof: `/api/summaries` stores client paper dicts) |
| TEST-1 | All new API routes covered by test file; frontend wiring test updated |

---

## Implementation order (recommended)

1. **D1, D2** — DB migration (no deps, unlocks all reference routes)
2. **FP3-B** — Settings routes + tests (self-contained, small, easy confidence-builder)
3. **FP2-B** — References routes + tests (depends on D1/D2)
4. **FP1-B additions** — Filter test + discover-terms routes + tests
5. **FP4-B** — save-as-list route + tests
6. **web/app.py** — register all new routers
7. **Frontend** — tabbed layout shell first, then tab-by-tab: Settings → References → Filters → Search enhancements
8. **Frontend wiring tests** — update `test_the_client_calls_the_endpoints_that_matter` and element-id test

---

## Open questions / risks

| Risk | Mitigation |
|------|-----------|
| TextFiltersWidget in desktop is a custom PyQt6 widget — the web equivalent is the most complex UI piece | Build it as a self-contained JS component; stub it with a single textarea (comma-sep keywords) if complexity blocks progress, then upgrade |
| PDF proxy could time out on large papers | Hard 30s timeout; return partial data or 504 with clear message |
| Discover-terms LLM call could be slow (>30s on large input) | Submit as a background job same as summaries; poll for result |
| YAML write in container: file may be read-only if volume not mounted | Surface a clear error from the PUT endpoint; document that settings writes require a writable mount |
