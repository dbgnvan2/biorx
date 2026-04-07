# pub_sources_spec.md

## Purpose

Implement the **publication source layer** for the app.

This spec covers only:
- source adapters
- source selection logic
- source picker behavior (`All` or per-source checkboxes)
- normalization
- deduplication
- ranking inputs related to source choice
- licensing / full-text access rules
- caching
- tests

This spec **does not** cover:
- general GUI layout
- general filter UI
- unrelated search controls
- result rendering

---

## Core design decision

Use **Europe PMC** as the default primary discovery source.

Do **not** use PubMed as a co-equal default search source in v1, because Europe PMC already includes PubMed/MEDLINE abstracts and adds more content such as PMC-derived material and preprints. PubMed support should exist only as an **optional enrichment adapter**.

---

## v1 source set

### Required in v1
1. **Europe PMC**
   - Primary search/discovery source
   - Main source for biomedical / stress / inflammation / human development overlap

2. **OSF / PsyArXiv**
   - Psychology / behavioral-science preprint source
   - Used to fill gaps not well covered by Europe PMC

3. **Crossref**
   - Metadata enrichment only
   - DOI resolution, publisher metadata, license metadata, funder metadata, ORCID/ROR when available

4. **Unpaywall**
   - OA lookup only
   - Resolve best legal open-access location after DOI is known

### Optional adapters behind feature flags
5. **bioRxiv / medRxiv**
   - Direct preprint freshness source
   - Useful when "latest preprints" matters

6. **PubMed / NCBI E-utilities**
   - Optional enrichment only
   - PMID / PMCID lookup, PubMed-native workflows, later MeSH support

7. **PMC OA services**
   - Optional full-text acquisition source
   - Only for clearly reusable OA content

8. **OpenAlex**
   - Optional graph / related-works / citation expansion source
   - Not a v1 primary search source

### Explicitly not in v1
- **Frontiers adapter**
- **SSRN adapter**
- individual publisher APIs unless separately approved

---

## Source picker requirements

The app already has GUI and filter sections. Add only a compact **Pick Sources** control with:

- one checkbox for **All Sources**
- one checkbox per enabled source

### Source picker options
- `All Sources`
- `Europe PMC`
- `PsyArXiv`
- `bioRxiv/medRxiv` (only if feature enabled)
- `PubMed` (only if feature enabled)
- `OpenAlex` (only if feature enabled)

Do **not** show metadata-only adapters in the picker:
- Crossref
- Unpaywall
- PMC OA

These are **internal enrichment services**, not user-search sources.

### Source picker logic

#### Default state
- `All Sources = ON`
- all individual visible source checkboxes = OFF / visually disabled or unchecked depending on UI approach

#### Behavior
1. If `All Sources` is ON:
   - query all **enabled search sources** allowed in the current build
   - ignore individual source selections

2. If any individual source checkbox is turned ON:
   - automatically turn `All Sources` OFF

3. If all individual source checkboxes are turned OFF manually:
   - automatically restore `All Sources = ON`
   - never allow a state with zero active sources

4. Metadata-only adapters must still run internally when needed:
   - Crossref after DOI lookup or metadata gaps
   - Unpaywall after DOI is known
   - PMC OA only when full text is requested and reusable

### Internal representation

Use a normalized internal source selection model:

```ts
export type SearchSource =
  | 'europepmc'
  | 'psyarxiv'
  | 'biorxiv_medrxiv'
  | 'pubmed'
  | 'openalex';

export interface SourceSelection {
  all: boolean;
  selected: SearchSource[];
}
```

Interpretation:
- if `all === true`, ignore `selected`
- if `all === false`, `selected` must contain at least one source

---

## Source routing rules

### Default routing
If `All Sources` is ON:
1. search Europe PMC
2. search PsyArXiv when enabled
3. search bioRxiv/medRxiv only if feature enabled and source enabled
4. do **not** search PubMed unless explicitly enabled in config and selected
5. do **not** search OpenAlex as primary discovery unless explicitly enabled and selected
6. then run internal enrichment:
   - Crossref
   - Unpaywall
   - PMC OA if needed

### Smart routing hints
These are routing hints, not hard exclusions.

#### Europe PMC should always be queried for:
- stress
- inflammation
- cortisol
- immune
- cytokines
- psychoneuroimmunology
- developmental psychopathology
- human development with biomedical overlap

#### PsyArXiv should be queried for:
- psychology
- behavioral science
- social psychology
- developmental psychology
- personality
- cognition
- clinical psychology
- psychometrics

#### bioRxiv/medRxiv should be queried for:
- latest preprints
- new papers
- recent preprints
- monitoring topics on a schedule

#### PubMed should not be in default v1 request flow
Reason:
- high overlap with Europe PMC
- more duplicate handling
- limited incremental value in v1

---

## Adapter contract

Every search-capable source adapter must implement the same interface.

```python
from typing import Protocol, Sequence

class RawRecord(dict):
    pass

class SearchAdapter(Protocol):
    source_name: str

    def search(self, query: str, page: int = 1, page_size: int = 25) -> Sequence[RawRecord]:
        ...

    def get_by_id(self, identifier: str) -> RawRecord | None:
        ...
```

### Required search adapters
- `EuropePmcAdapter`
- `PsyArxivAdapter`

### Optional search adapters
- `BiorxivMedrxivAdapter`
- `PubmedAdapter`
- `OpenAlexAdapter`

### Required enrichment adapters
- `CrossrefAdapter`
- `UnpaywallAdapter`

### Optional enrichment adapters
- `PmcOaAdapter`

---

## Canonical record schema

Keep the schema tight. Only fields needed for source selection, dedupe, ranking, OA access, and citation display are required here.

```json
{
  "canonical_id": "",
  "title": "",
  "abstract": "",
  "authors": [
    {
      "display_name": "",
      "orcid": "",
      "sequence": 1
    }
  ],
  "year": 0,
  "published_date": "",
  "document_type": "article|preprint|review|trial|other",
  "is_preprint": false,
  "journal_or_server": "",
  "doi": "",
  "pmid": "",
  "pmcid": "",
  "source_url": "",
  "best_oa_url": "",
  "pdf_url": "",
  "license": "",
  "oa_status": "",
  "subjects": [],
  "keywords": [],
  "source_hits": [
    {
      "source": "europepmc",
      "source_record_id": "",
      "fetched_at": ""
    }
  ],
  "flags": {
    "retracted": false,
    "corrected": false,
    "fulltext_reusable": false
  }
}
```

---

## Deduplication rules

Use deterministic dedupe in this order.

### Identity precedence
1. normalized DOI
2. PMID
3. PMCID
4. normalized title + first author + year

### Merge precedence by field
- `title`: Europe PMC > Crossref > source-native fallback
- `abstract`: Europe PMC or source-native abstract > Crossref abstract
- `doi`: Crossref may fill missing DOI
- `best_oa_url`: Unpaywall preferred
- `license`: Unpaywall or PMC OA preferred
- `is_preprint`: preserve explicitly
- `journal_or_server`: preserve the most specific value
- `source_hits`: append all matching sources

### Important rule
Do not collapse a preprint and a later journal article into separate unrelated records if they clearly represent the same work. Keep one canonical record with provenance from both, but preserve:
- `is_preprint`
- publication status
- source provenance

---

## Ranking inputs related to sources

Full ranking is outside this spec. Only source-related ranking requirements are defined here.

### Source trust weights
Use as ranking inputs, not hard filters.

```text
peer-reviewed article from Europe PMC / PubMed      1.00
PMC OA-backed article                               0.98
Crossref-only metadata                              0.85
PsyArXiv preprint                                   0.75
bioRxiv / medRxiv preprint                          0.75
OpenAlex-only fallback                              0.70
```

### Source-related ranking rules
1. Prefer peer-reviewed records over preprints by default
2. If the user explicitly selects a preprint source, remove or reduce the preprint penalty
3. Prefer records with DOI over records without DOI
4. Prefer records with abstract over records without abstract
5. Prefer records with legal OA access over records without accessible full text
6. Penalize retracted or corrected records when flags are present

---

## Full-text and licensing rules

### Hard rules
1. Metadata may always be stored if API terms allow it
2. Full text may be downloaded only when clearly permitted
3. Do not assume that a record being in PMC means reusable full text
4. Use Unpaywall only to locate legal OA versions
5. Use PMC OA services only for clearly reusable OA-subset content
6. If reuse status is unclear, store:
   - title
   - abstract
   - identifiers
   - source URL
   - OA URL if available
   - license state
   and do **not** download full text

### Full-text decision function

```python

def can_download_fulltext(record: CanonicalRecord) -> bool:
    return bool(
        record.flags.get("fulltext_reusable")
        and record.best_oa_url
    )
```

---

## Caching rules

### Cache layers
- `raw_search_cache`: 24 hours
- `normalized_record_cache`: 7 days
- `oa_lookup_cache`: 7 days
- `id_resolution_cache`: 30 days

### Rules
1. Cache raw API responses by source + query + page
2. Cache normalized canonical records separately from raw responses
3. Cache OA lookups only after DOI normalization
4. If a source is unavailable, serve cached data when possible
5. Crossref and Unpaywall failures must not block search results

---

## Config requirements

Use feature flags so the code can ship with a smaller or larger source set.

```yaml
publication_sources:
  europepmc:
    enabled: true
    default_selected: true

  psyarxiv:
    enabled: true
    default_selected: true

  biorxiv_medrxiv:
    enabled: false
    default_selected: false

  pubmed:
    enabled: false
    default_selected: false

  openalex:
    enabled: false
    default_selected: false

  crossref:
    enabled: true

  unpaywall:
    enabled: true

  pmc_oa:
    enabled: false
```

### Required config behavior
- disabled sources do not appear in the picker
- disabled sources cannot be invoked internally except where explicitly allowed
- metadata-only adapters do not appear in the picker regardless of enabled state

---

## Required orchestration flow

```text
1. Read source picker state
2. Resolve active search sources
3. Query active search sources
4. Normalize each source result into canonical schema
5. Dedupe across sources
6. Enrich with Crossref where DOI or metadata is missing/incomplete
7. Resolve OA via Unpaywall if DOI exists
8. Optionally resolve PMC OA full text if requested and reusable
9. Apply source-related ranking inputs
10. Return canonical records
```

### Important constraint
Do not run Crossref, Unpaywall, or PMC OA as independent search sources.
They are enrichment steps only.

---

## Error handling

Implement typed errors:

```python
class SourceUnavailableError(Exception): ...
class RateLimitedError(Exception): ...
class SchemaDriftError(Exception): ...
class NotReusableFullTextError(Exception): ...
class IdentifierMismatchError(Exception): ...
```

### Fallback behavior
- if Europe PMC fails, continue with remaining selected sources and cached Europe PMC results if available
- if PsyArXiv fails, do not fail the whole query
- if Crossref fails, return records without enrichment
- if Unpaywall fails, return records without OA enrichment
- if PMC OA fails, keep metadata only

---

## Minimum test suite

### Source picker tests
1. default state is `All Sources = ON`
2. turning on one specific source turns `All Sources = OFF`
3. turning off the last selected source restores `All Sources = ON`
4. disabled sources are not shown
5. Crossref / Unpaywall / PMC OA never appear in the picker

### Routing tests
1. `All Sources = ON` queries all enabled search adapters
2. selecting only Europe PMC queries only Europe PMC as a search source
3. selecting Europe PMC + PsyArXiv queries only those two search sources
4. metadata-only adapters still run when needed

### Dedupe tests
1. same DOI from two sources becomes one canonical record
2. DOI-missing records dedupe by PMID when available
3. fallback title + first author + year dedupe works
4. preprint + later article can merge into one canonical record with provenance preserved

### Enrichment tests
1. Crossref fills missing DOI when resolvable
2. Unpaywall resolves `best_oa_url` when DOI exists
3. no Unpaywall call is made if DOI is missing
4. PMC OA is used only when feature enabled and reuse is allowed

### Domain query tests
1. `stress inflammation adolescence` returns Europe PMC results
2. `attachment developmental psychology emotion regulation` triggers PsyArXiv when enabled
3. `latest preprints on cortisol` triggers bioRxiv/medRxiv only when feature enabled and selected or All is on

---

## Build order

1. Europe PMC adapter
2. Crossref enrichment
3. Unpaywall enrichment
4. PsyArXiv adapter
5. source picker logic (`All` + checkboxes)
6. dedupe layer
7. optional bioRxiv/medRxiv adapter
8. optional PubMed adapter
9. optional PMC OA adapter
10. optional OpenAlex adapter

---

## Direct implementation instruction

```md
Implement the publication source layer.

Scope:
- Add source selection only: `All Sources` or per-source checkboxes
- Do not redesign existing GUI or filter sections
- Add source adapters, normalization, dedupe, source-aware routing, enrichment, and tests

Rules:
1. Europe PMC is the default primary discovery source.
2. PsyArXiv is the psychology-specific search source.
3. Crossref and Unpaywall are enrichment-only adapters and must not appear in the source picker.
4. PubMed is optional and must not be in the default v1 request path.
5. Use deterministic dedupe: DOI, then PMID, then PMCID, then title+first-author+year.
6. Never allow zero active search sources.
7. If all source checkboxes are off, automatically restore `All Sources`.
8. Only download full text when reuse is clearly allowed.
9. Preserve preprint vs peer-reviewed status in the canonical record.
10. Ship with contract tests for adapter behavior and source picker state logic.
```

---

## Final design summary

For v1, the app should search **Europe PMC** and **PsyArXiv**, enrich with **Crossref** and **Unpaywall**, and expose only a simple source picker:
- `All Sources`
- or specific search-source checkboxes

Keep PubMed, PMC OA, bioRxiv/medRxiv, and OpenAlex behind feature flags.
