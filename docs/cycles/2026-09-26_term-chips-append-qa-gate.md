# QA Gate — Discover-term click appends a group and extends the filter name

- **Date:** 2026-09-26
- **Range reviewed:** `origin/main..HEAD` (2 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1328 passed, 1 skipped**

RANGE:       origin/main..HEAD (caller-supplied)
COMMITS:     2 commits:
             4332fd8 docs: two cost-dialog problems found by the first real review run
             1998932 feat(filters): a term click adds a group and extends the filter's name
APPLICABLE:  P5 (sibling drift across the two filter-save paths), P2 (silent drop),
             P19 (producer/consumer drift: FILTER_NAME_MAX vs the route's max_length),
             P4 (hardcoded constant), P27 (vacuous test), P19-corollary
             (source-text assertion), P29 (floor assertion)
CHECKED:     P2, P4, P5, P19, P27, P29, plus P19-corollary and P29 over every new
             test. Read web/static/app.js (saveFilter, saveFilterAs, saveTermFilter,
             createFilterFromTerm, addTermAsGroup, freeFilterName, appendedFilterName,
             renderDiscoverChips, reloadFilterList, loadSearchFilters), web/routes_filters.py,
             src/user_store.py (upsert_filter, delete_filter); grepped every
             api("PUT")/api("POST") to /api/filters call site in app.js; ran the full
             suite; proved the new tests live by mutation (broke appendedFilterName and
             removed both `state.activeFilterId = saved.id` lines → 6 tests went red,
             then restored to a clean tree and re-ran green).
NOT COVERED: same-user cross-tab race on a rename (two tabs renaming the same filter
             simultaneously), auth beyond the route-level check, injection/security,
             the desktop GUI surface (gui.py / filters.json — it does not PUT to the
             web /api/filters route), the docs commit's cost-dialog claims (it is a
             TODO entry, not code).

---

## The two hazards the feature had to handle — both correctly handled

**(a) A rename changes the filter id — SOUND, and the P5 sibling is fully closed.**
`web/routes_filters.py:update_filter` does `upsert_filter` on the new name and
deletes the old row when the id changed, so the id the PUT returns is the one to
keep. A grep of `web/static/app.js` finds exactly two `api("PUT", "/api/filters/…")`
call sites — `saveFilter` (app.js:1642) and `saveTermFilter` (app.js:1961) — and
**both** now do `state.activeFilterId = saved.id`. There is no third rename path:
`saveFilterAs` and `createFilterFromTerm` are POSTs (always create, and keep
`created.id`), and `deleteFilter` nulls the id. The two PUT sites are pinned by
`test_dc4_both_save_paths_keep_the_id_put_returns`, which is parametrized over
exactly those two functions, so a future third site is a conscious addition, not a
missed sibling today.

**(b) The rename is an upsert, so a colliding appended name overwrites — SOUND.**
`appendedFilterName` makes the appended name free against every *other* filter's
name (`addTermAsGroup` builds `others` from `state.filters` minus the open filter,
which is exactly the "own name is not taken" case the test pins), and returns `null`
rather than a server-truncated name when the append would pass the 200-char limit.
The null path still adds the term as a group and still saves — the message says the
name was left as-is and why, so nothing is dropped silently (P2).

## Test quality — none of the new tests are vacuous

Proved by mutation, not argument (P27). I broke `appendedFilterName`'s separator
and deleted both `state.activeFilterId = saved.id` lines, ran the dc4 group, and got
**6 failures** (`test_dc4_the_term_is_appended_to_the_filter_name`,
`test_dc4_the_open_filters_own_name_is_not_counted_as_taken`,
`test_dc4_an_appended_name_never_lands_on_another_filter`,
`test_dc4_a_name_past_the_server_limit_is_refused_not_cut_off`, and both params of
`test_dc4_both_save_paths_keep_the_id_put_returns`); restored the tree and re-ran
green (9 passed). The behavioural tests execute the real functions in node via
`_node_eval` (which skips, not fakes, when node is absent, and `_js_block` asserts
the block exists so a rename fails loudly). The source-text tests strip comments
first (`_js_without_comments`, itself guarded by `test_the_comment_stripper_works`),
so they match code, not prose. Every assertion is an exact value, not a floor (P29
clean).

## Findings (ranked)

None at medium or higher confidence. The hazards the commit exists to prevent are
both handled, the sibling class is closed, and the tests are live. The following are
non-blocking and pre-existing or cosmetic; recorded so they are not silently dropped:

1. **P19-corollary (over-literal, low) · tests/web/test_frontend_wiring.py:995.**
   `assert "state.activeFilterId = saved.id" in body` is a bare-substring match: it
   would also pass if the code became `state.activeFilterId = saved.id + 1` (the
   needle is a prefix of a longer string). Not a plausible bug, and the repo's
   documented front-end test style is source-text; a browser-driven click-click test
   is the stronger alternative but out of scope here.

2. **Comment inaccuracy (low) · web/static/app.js:1891-1904.** "The server silently
   cuts names at 200 characters" misstates the mechanism: the route's
   `FilterBody.name = Field(max_length=200)` *rejects* a longer name with 422; the
   `[:200]` truncation in `upsert_filter` is only reachable from the seed path, not
   the API. The JS behaviour (refuse >200, keep the old name) is correct regardless;
   only the comment's rationale is imprecise.

3. **Sibling with the same hazard, out of range (P5/P2, pre-existing).**
   `saveFilterAs` (app.js:1654) still POSTs a caller-prompted name with no free-name
   step, so a "save as" onto an existing name overwrites that filter — the exact
   overwrite the chip path now prevents. Already recorded in TODO.md; not introduced
   or touched by this diff, so not a gate block.

4. **`freeFilterName` still defaults `maxLen=80` (P4, pre-existing).**
   `createFilterFromTerm` calls `freeFilterName(term, …)` with no maxLen, so a newly
   created filter truncates a long discover term at 80 chars while the server allows
   200. The new `appendedFilterName` correctly passes `FILTER_NAME_MAX + 1`; the
   sibling creation path does not. Cosmetic, pre-existing.

5. **Debounce drops a rapid second click (P2, pre-existing).** `state.discoverSaving`
   returns silently while a save is in flight, so two quick term-clicks lose the
   second. Pre-existing guard, now on the more click-heavy left-click path.

---

## Verdict: APPROVED

Clean against P1–P36, of which P2, P4, P5, P19, P27, P29 (plus P19-corollary) were
applicable to this change. The two hazards the feature exists to handle — the id
changing on rename and the upsert-overwrite on a colliding appended name — are both
correctly handled, and the P5 sibling class is fully closed (both PUT sites keep the
returned id, pinned by a parametrized test). The full suite is green (1328 passed,
1 skipped), and the new tests are live (proven red by mutation, then green on
restore). Findings 1–5 are non-blocking; 1, 3, 4, 5 are pre-existing or cosmetic and
recommended for a follow-up round, not a reopen of this gate.
