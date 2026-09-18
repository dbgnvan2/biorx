# Plan — Web Settings tab: per-user settings instead of server config editing
**Date:** 2026-09-17
**Replaces:** FP3 in `docs/web_parity_spec_2026-09-17.md` (FP3-A, FP3-B, SEC-1)
**Status:** approved 2026-09-17; implemented

## Problem

The web Settings tab (commit `9a2366e`, `web/routes_settings.py`) reads and overwrites the
server's `sources_config.yaml` and `llm_config.yaml`. Every signed-in user can change the
configuration for everyone (enabled sources, provider base URLs, the daily summary cap on the
owner's key) and can read the owner's contact email.

Decision (Dave, 2026-09-17): web users get their own settings, stored locally for that user.
They never edit the server's config files. The server files stay owner-only, edited on the
machine (or via env vars on a deployment).

The desktop GUI's Settings tab is not changed: it runs on the owner's machine and editing
the local YAML there is correct.

## What each user can set (stored in their browser's localStorage)

| Setting | Today | After |
|---|---|---|
| LLM provider, API key, model | Already per-user in localStorage (`f031d12`), in a separate "LLM settings" panel in the header | Unchanged behaviour; panel moves into the Settings tab |
| Default sources (which are pre-checked in the Search and Filter source pickers) | Not configurable per user | Per-user, saved in localStorage; choices limited to the sources the server has enabled |

Not user-settable, stays server-side: which sources are enabled at all, contact email,
provider base URLs, token budget, daily cap.

## Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| PS1 | No web route reads or writes a server config file. `/api/settings/*` returns 404 for GET and PUT. | `tests/web/test_settings_routes.py` rewritten: `test_ps1_settings_routes_removed` (GET and PUT on both filenames → 404) |
| PS2 | A PUT attempt leaves `sources_config.yaml` and `llm_config.yaml` byte-identical (dirty-state check). | `test_ps2_put_does_not_touch_config_files` — reads both files before and after |
| PS3 | No route in the app exposes the contents of either config file. | `test_ps3_no_route_returns_config_text` — enumerates `app.routes`, asserts no path contains `settings/` + a YAML filename; and `/healthz` response has no `contact_email` key |
| PS4 | The client no longer calls `/api/settings` and the YAML editor elements are gone. | `test_frontend_wiring.py`: expected endpoint set 23 → 21; element-id test drops `settings-file-select`, `settings-editor`, `btn-settings-save`, `btn-settings-reload` |
| PS5 | The Settings tab contains the LLM panel (provider, key, model) and the default-sources picker. | `test_frontend_wiring.py::test_ps5_settings_tab_contains_llm_and_sources` — parses `index.html`, asserts `#key-provider`, `#api-key`, `#preferred-model`, `#default-sources` are descendants of `#panel-settings` |
| PS6 | Saved default sources are applied to both pickers on load; a saved source the server no longer enables is dropped, not shown. Empty/missing/corrupt storage falls back to "all enabled checked". | `test_frontend_wiring.py::test_ps6_default_sources_logic` — runs a pure function `applyDefaultSources(enabled, saved)` from `app.js` in node (same pattern as the existing `safeUrl` test) with: normal case, stale id, empty, corrupt JSON. Skipped when node is absent. |
| PS7 | localStorage failures (private window, blocked storage) don't break the page. | PS6 corrupt/empty cases for the parser; `test_frontend_wiring.py::test_ps7_storage_access_is_guarded` asserts every default-sources storage call is inside a `try` |
| PS8 | Route count guard updated. | `tests/web/test_auth.py`: `PROTECTED_ROUTE_COUNT` 30 → 28 |
| PS10 | (found while building) Opening a saved filter in the web UI shows its saved fields, including its own sources. The parity commit read `f.filter`, which `GET /api/filters` never returns, so every filter opened blank and Save wiped it. | `test_frontend_wiring.py::test_ps10_client_reads_filters_in_the_shape_the_api_returns` (real route output through the client's `filterFields` in node), `test_ps10_select_filter_reads_through_filter_fields` |
| PS9 | Spec updated so FP3 describes per-user settings. | `docs/web_parity_spec_2026-09-17.md` FP3 section rewritten; `docs/spec_coverage_webapp.md` rows for FP3-A/FP3-B/SEC-1 replaced by PS1–PS9 |

## Implementation order

1. Tests PS1, PS2, PS3 first (they fail now: the routes exist).
2. Delete `web/routes_settings.py`; remove its registration in `web/app.py`. PS1–PS3 pass; update PS8.
3. `index.html`: remove the YAML editor; move the `#settings` LLM panel into `#panel-settings`; remove the header "LLM settings" toggle; add `#default-sources`.
4. `app.js`: remove settings-editor code; add `applyDefaultSources` (pure) + load/save wrappers in `try/catch`; call it when rendering both pickers.
5. Wiring tests PS4–PS6.
6. Docs (PS9), CHANGELOG entry.
7. Full suite; click through the Settings, Search and Filters tabs in the browser pane.

## Human review (not code-testable)

- Visual check that the Settings tab layout reads correctly and the pickers show the saved defaults after reload (step 7).
- PS7: the test checks the `try` is there, not the behaviour in a storage-blocking browser. Not run in one.

## Adjacent issues found, not fixed

- `/healthz` is unauthenticated and returns `db_path` and `startup_warnings` — minor information disclosure on a shared deployment. Separate change.
- ~~`contact_email` in tracked `sources_config.yaml`~~ — done: line reverted, `BIORX_CONTACT_EMAIL` set in `~/.zshrc`.
- Static assets have no cache-busting: after a deploy, browsers keep the old `app.js`/`styles.css` until a hard reload (seen during the browser check).
- The "Use date range" checkbox in the Search tab has the same stacked layout the source pickers had.
- The spec gap that let this through: FP3 never said *whose* settings. Worth a line in `LEARNINGS.md` at check-off.
