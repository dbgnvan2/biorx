# Implementation plan — gate-notes gate TODO items (2026-10-08, GN4–GN5)

**Request:** "fix the TODO items from today's gates" (third time).
**Status:** APPROVED 2026-10-08 ("continue until you are finished"); built.
GN4–GN6 done, each test mutation-checked.
**Found during the build — GN6:** `tests/test_db_concurrency.py::test_each_thread_gets_its_own_connection`
failed once in the full suite under load and passed 8 of 8 times on its own. It
compared `id()` values, and a finished thread's connection can be freed and its
id reused by the next thread. The test now keeps the connection objects and
compares them with `is`. It still fails when connections are shared
(mutation-checked). Test-only change.
**Source:** `TODO.md`, "Gate-notes gate" (three items, from
`docs/cycles/2026-10-08_gate-notes-qa-gate.md`). The other sections from
today are closed.
**Touches protected retrieval code:** none. `src/filtering.py` and
`src/sources/config.py` are not on the protected list. The orchestrator is
not changed.

---

| ID | Item | Fix | Test |
|---|---|---|---|
| GN4 | `filtering.keyword_fields` is a near-copy of `schema.keyword_list`. | `keyword_fields` becomes a call to `keyword_list`: one function reads keywords for the adapters and the filter. Behaviour is unchanged, apart from stripping spaces, which matching already ignores. | Existing `test_kw5_*`, `test_kw6_*`, `test_gn2/3_*` pass unchanged; `tests/test_filtering.py::test_gn4_filter_uses_the_shared_reader` (a single-string keyword field and a padded keyword match as one keyword) |
| GN5 | Three hand-kept source lists: `config._SEARCH_SOURCES` (7, incl. `openalex`), the orchestrator's adapters (6) and the limit summary's lists (6). The GN1 test ties only the last two together, and its `len(registered) >= 6` is a floor. | Name the known gap in config: `SOURCES_WITHOUT_ADAPTER = ("openalex",)` in `src/sources/config.py`. `openalex` stays in `_SEARCH_SOURCES`, so the M31 warning still fires when it is enabled. The GN1 test drops the floor and asserts the exact chain: (1) the registered adapters equal `_SEARCH_SOURCES` minus `SOURCES_WITHOUT_ADAPTER`; (2) they also equal the union of the summary's lists. Adding a source to any one list without the others fails the test. | `tests/test_limit_summary.py::test_gn1_every_registered_source_is_classified` (rewritten); mutation-checked by adding a fake name to `_SEARCH_SOURCES` and by dropping one from the summary's lists |

## Build order

GN4 → GN5 → docs (CHANGELOG, spec coverage, TODO section removed) → full
suite → `/chdp` Hermes gate (report only; below-medium notes into TODO.md
with the gate commit) → push → CI.
