# Implementation plan — match author keywords in the "title or abstract" box (2026-10-08, KW1–KW10)

**Request:** "plan the author keywords change".
**Status:** PLAN — awaiting approval.
**Touches protected retrieval code (W1.a, flagged):**
- `src/sources/schema.py` (KW1)
- `src/sources/europepmc.py` and `src/sources/osf.py` (KW2)
- `src/sources/dedup.py` (KW3)
- `src/sources/query_builder.py` (KW4)

The drift baseline is advanced once for the batch, with the reason.

---

## 0. Today

PubMed's Title/Abstract search also matches the authors' own keywords. This
app matches only the title and abstract. For `internal family systems`, 3 of
PubMed's 18 papers from 2016–2026 have the phrase only in their keywords, so
the app never shows them.

Live counts (Europe PMC, 2026-10-08):

| Query | Papers |
|---|---|
| `TITLE_ABS:"internal family systems"` | 28 |
| `KW:"internal family systems"` | 8 |
| `TITLE_ABS:… OR KW:…` | 31 |
| `TITLE_ABS:… AND SRC:MED` (what the app reads as PubMed) | 21 |
| `(TITLE_ABS:… OR KW:…) AND SRC:MED` | **24 — the same as PubMed's own 24** |
| PubMed `"internal family systems"[tiab] OR [ot]` | 24 |
| `loneliness`, last year: title/abstract → with keywords | 3,257 → 3,310 (+1.6%) |
| `cooperati*`, last month: title/abstract → with keywords | 743 → 805 (+8%) |

Making this work needs four changes, not just a query change:
1. **The query** (`query_builder._group_to_lucene`) sends `TITLE_ABS:` only.
2. **The local filter** (`filtering.text_group_matches`, `within_matches`)
   checks only the title and abstract, so it would drop a paper that Europe
   PMC returned for a keyword match.
3. **The record never carries keywords to the filter.** The adapters read
   them (`CanonicalRecord.keywords`), but `CanonicalRecord.to_dict()` leaves
   them out, and the filter works on that dict.
4. **Keywords are cut and not merged:**
   - the Europe PMC and OSF adapters keep only the first 10
     (`keywords[:10]`, `tags[:10]`), so a match on the 11th would be dropped;
   - de-duplication keeps the first source's keywords and ignores the rest.

## 1. Behaviour

- The **title-or-abstract box** (Search tab "Words in title or abstract"; the
  Filters editor "Title or abstract words") matches the title, the abstract
  **or the authors' keywords**. It is renamed "Words in title, abstract or
  keywords" / "Title, abstract or keywords words".
- The separate **Title** and **Abstract** boxes keep their exact meaning.
- **Search within these results** uses the same three fields, as it already
  shares the box's rules.
- Matching rules are unchanged: commas mean any of, AND means all parts, a
  trailing `*` is a wildcard, punctuation counts as a space. Each AND part
  must be found within one field (TD5), and **each keyword counts as its own
  field**, so a phrase cannot join the end of one keyword to the start of the
  next.
- Where keywords come from:
  - Europe PMC and PubMed: the authors' keywords (`keywordList`; MeSH terms
    are not included);
  - PsyArXiv and SocArXiv: the authors' tags;
  - arXiv and bioRxiv/medRxiv: none (their APIs give none).
- The query sent:
  - Europe PMC and PubMed: each part becomes `(TITLE_ABS:x OR KW:x)`;
  - arXiv: unchanged (no keyword field);
  - PsyArXiv and SocArXiv: unchanged (they are narrowed by Title only).
- The paper detail view shows a **Keywords** line when the paper has any, so
  a match that is not in the title or abstract can be seen.
- **Discover:**
  - its live Europe PMC count uses the same query, so it now includes keyword
    matches, and its wording changes to "title, abstract or keywords";
  - its local count (in the sampled titles and abstracts) is unchanged,
    because its terms are chosen from those texts.

## 2. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| KW1 | `CanonicalRecord.to_dict()` includes `keywords` (a list), and `from_dict` reads it back. *Protected.* | `tests/test_adapters.py::test_kw1_keywords_in_the_dict` |
| KW2 | Europe PMC and OSF keep every keyword (no `[:10]`). Real scale (P9): a record with 25 keywords keeps all 25, and a match on the 25th is kept by the filter. *Protected.* | `tests/test_adapters.py::test_kw2_all_keywords_kept` (both adapters) |
| KW3 | De-duplication merges keywords: union, keeping order, with case-insensitive duplicates removed. A bioRxiv record with no keywords merged with the Europe PMC record for the same paper ends up with Europe PMC's keywords, whichever source came first. *Protected.* | `tests/test_dedup.py::test_kw3_keywords_merged` (both orders) |
| KW4 | Europe PMC query: each title-or-abstract part becomes `(TITLE_ABS:x OR KW:x)`, including quoted phrases, wildcards, AND parts and split hyphen wildcards. The Title and Abstract boxes are unchanged, and so are arXiv and OSF. *Protected.* | `tests/test_query_builder.py::test_kw4_*`; existing TA/TD1/HW tests updated to the new clause (listed in the commit) |
| KW5 | Local filter: the title-or-abstract box and search-within match the title, the abstract or any one keyword. | `tests/test_filtering.py::test_kw5_keyword_match_kept`, `test_kw5_within_uses_keywords` |
| KW6 | Adversarial (P7): a phrase split across two keywords (`["internal family", "systems theory"]` for "family systems") does not match. The Title box does not match a keyword-only paper. A paper with no keywords behaves as before. | `test_kw6_*` |
| KW7 | Superset (P7): for each KW4 case, every paper the local filter keeps is matched by the query sent. Checked with a small in-test matcher for TITLE_ABS/KW, as in HW2. | `tests/test_query_builder.py::test_kw7_query_is_a_superset_of_the_filter` |
| KW8 | Page: the two labels renamed; the detail view shows "Keywords: …" with textContent when there are keywords and nothing when there are none; Discover's wording updated. | `tests/web/test_frontend_wiring.py::test_kw8_*` (node-run) |
| KW9 | Route: a fake orchestrator returns a keyword-only paper, and `/api/searches` keeps it. A saved filter run keeps it. Search-within keeps it. | `tests/web/test_searches_routes.py::test_kw9_*` |
| KW10 | Live: `internal family systems`, All years, Europe PMC + PubMed, on a local server. It should show 31 Europe PMC hits, with the 3 keyword-only PubMed papers present (PMIDs 34950063, 35002818, 42325314). Drift baseline advanced; CI green. | local browser check (status report); `tests/web/test_no_retrieval_drift.py` |
| R | Existing searches without keyword matches give the same results. The full suite passes, apart from tests that pin the old `TITLE_ABS`-only clause (updated, and listed in the commit). | full suite |

## 3. Build order

KW1 → KW2 → KW3 (the record carries keywords) → KW5/KW6 (local filter) →
KW4/KW7 (query) → KW9 (routes) → KW8 (page) → docs (CHANGELOG, spec
coverage, TODO item removed) → drift baseline → full suite → KW10 live check →
`/chdp` Hermes gate → push → CI.

## 4. Owner decisions (defaults chosen)

- **Always on, no switch.** The box matches keywords for every search, as
  PubMed does. A switch would add a setting that few would change. Say if you
  want one.
- **Saved filters change too.** Existing filters will find more papers (from
  about +2% for `loneliness` to +8% for `cooperati*`). That uses more of
  each source's limit; the limit summary already reports this.
- **MeSH terms are not matched.** They are assigned by indexers, not
  written by the authors, and PubMed's Title/Abstract does not search them
  either.

## Adjacent issues found, not fixed

- `CanonicalRecord.to_dict()` also leaves out `subjects` (only the first is
  sent, as `category`). Nothing filters on the others today.
