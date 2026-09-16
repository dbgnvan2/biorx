# Task: Add an arXiv source + headless CLI to biorx

You are working in `/Users/davemini2/ProjectsLocal/biorx` (Python 3.9+). This repo is a
multi-source paper-discovery app. It already has the hard parts — a `SearchAdapter` protocol,
a `SourceOrchestrator` that routes → dedupes → enriches → ranks, and adapters for Europe PMC,
PubMed, PsyArXiv, SocArXiv, bioRxiv/medRxiv, plus Crossref/Unpaywall enrichment.

What's missing: an **arXiv** source (none of the current sources index the CS / LLM-agent /
agent-based-simulation literature), and a **headless CLI** (the multi-source layer is only
reachable from the PyQt6 GUI). Both are needed so a cron job can drive it.

Read these before writing code (they define the patterns and conventions):
- `CLAUDE.md` (project standards; note the `~/.claude/standards/` files it names)
- `src/sources/base.py` (the adapter protocol)
- `src/sources/biorxiv_medrxiv.py` (the cleanest adapter to copy the shape from)
- `src/sources/orchestrator.py` (`_register_adapters`, `_build_query`, `_search_source`)
- `src/sources/config.py` (how sources are registered/feature-flagged)
- `src/sources/query_builder.py` (how a `filter_dict` becomes a source query)
- `src/sources/schema.py` (`CanonicalRecord`), `src/sources/errors.py` (typed exceptions)

Baseline: run `/opt/homebrew/bin/pytest tests/ -v` **before** and **after**; the whole suite
must stay green, and you add new tests.

---

## Change 1 — `src/sources/arxiv.py` (new): `ArxivAdapter`

Implement `ArxivAdapter` satisfying the `SearchAdapter` protocol.
`source_name = "arxiv"`, `source_trust_weight = 0.75` (preprint class, same as psyarxiv).

**`search(self, query: str, page: int = 1, page_size: int = 50) -> Sequence[RawRecord]`**
- GET `https://export.arxiv.org/api/query` with params:
  - `search_query=<query>`, `start=(page-1)*page_size`, `max_results=page_size`,
  - `sortBy=submittedDate`, `sortOrder=descending`.
- Send a **descriptive** User-Agent (e.g. `biorx/1.0 (mailto:davegalloway@me.com)`). arXiv
  throttles a generic `Mozilla/5.0` UA far harder than a descriptive one.
- Parse the Atom XML (namespace `http://www.w3.org/2005/Atom`) with `xml.etree.ElementTree`.
  Per `<entry>` extract: title, id (abs URL → arXiv id), published date, authors, summary
  (abstract), categories.
- Set `self.last_total` from the OpenSearch `<totalResults>` element (namespace
  `http://a9.com/-/spec/opensearch/1.1/`; default 0) so the orchestrator's pagination
  stop-condition works.
- Withdrawn papers: if the `<summary>` text contains "withdrawn" / "This paper has been
  withdrawn", skip that entry (do not return it).
- Rate/error handling: on HTTP 429 honour the `Retry-After` header (sleep then retry, up to ~3
  attempts) and raise `RateLimitedError` if it persists; on connection error / 5xx raise
  `SourceUnavailableError`. Space requests ≥3 s apart.
- Return a list of raw dicts keyed: `arxiv_id` (full, incl. version), `title`, `abstract`,
  `authors` (list of name strings), `published` (YYYY-MM-DD), `categories` (list of terms).

**`normalize(self, raw) -> CanonicalRecord`**
- Strip any `vN` suffix for identity; `arxiv_id_full` keeps the version.
- `canonical_id = f"arxiv:{arxiv_id_no_version}"` — do NOT use `make_canonical_id`; the arXiv
  id is the identity. (The dedup layer's title+author+year fallback still merges a later
  journal version of the same work across sources.)
- `source_url = f"https://arxiv.org/abs/{arxiv_id_full}"`,
  `pdf_url = f"https://arxiv.org/pdf/{arxiv_id_full}"`.
- `journal_or_server = "arXiv"`, `is_preprint = True`, `document_type = "preprint"`,
  `oa_status = "open"`, `flags = RecordFlags(fulltext_reusable=True)`.
- `authors` → list of `AuthorRecord` (sequence = order). `subjects` = the arXiv categories.
- `published_date`/`year` from `published`. `doi`/`pmid`/`pmcid` empty.
- `source_hits = [SourceHit(source="arxiv", source_record_id=arxiv_id_full, fetched_at=iso_now)]`.

**`get_by_id(self, identifier) -> Optional[RawRecord]`**: return `None` (not needed).

## Change 2 — wire it in

- `src/sources/config.py`: add `"arxiv": {"enabled": True, "default_selected": False}` to
  `_DEFAULT_CONFIG["publication_sources"]`; add `"arxiv"` to `_SEARCH_SOURCES`; add
  `"arxiv": "arXiv"` to `SOURCE_LABELS`.
- `sources_config.yaml`: add an `arxiv: {enabled: true, default_selected: false}` block under
  `publication_sources`.
- `src/sources/orchestrator.py`:
  - `_register_adapters()`: `from .arxiv import ArxivAdapter; self._search_adapters["arxiv"] = ArxivAdapter()`.
  - `_SOURCE_TRUST["arxiv"] = 0.75`; `_SOURCE_LABELS["arxiv"] = "arXiv"`.
  - `_build_query()`: `if source_name == "arxiv": return build_arxiv_query(filter_dict)`.
    (No change to `_search_source` — arXiv uses the query-only branch, because the date range
    is baked into the query string below.)
- `src/sources/query_builder.py`: add `build_arxiv_query(filter_dict) -> str`.

**`build_arxiv_query` semantics** (mirror `build_europepmc_query`, swap field syntax):
- Field prefixes: title→`ti:`, abstract→`abs:`, both→`all:`, author→`au:`.
- Within a text_group: title terms OR-joined, abstract terms OR-joined, both terms OR-joined,
  then the three clause-sets AND-joined (same logic as `_group_to_lucene`). Multi-word phrase
  → `field:"phrase"`; single word → `field:word`. Groups are OR-joined.
- Authors (comma-split) → `au:"Lastname"` clauses, OR-joined, AND-ed with the text part.
- Date range: reuse `get_date_range(filter_dict)`; append
  `AND submittedDate:[YYYYMMDDHHmm TO YYYYMMDDHHmm]` (arXiv `YYYYMMDDHHmm`; start→`0000`,
  end→`2359`).
- If there is no text and no author clause, return just the `submittedDate:[... TO ...]` clause
  (a pure date-range query is valid on arXiv).
- Ignore `species`, `paper_type`, `license`, `published` (no arXiv equivalent — those filters
  are applied client-side).

## Change 3 — `agents/monitor.py` (new): headless CLI

Standalone script, **no PyQt6 import**. `sys.path.insert(0, str(Path(__file__).parent.parent))`
like the other agents. Expose both a `main()` (for CLI) and an importable runner function.

```
python agents/monitor.py --filter "Agent Simulation" [--dry-run] [--download-dir PATH] [--json PATH] [--max N]
python agents/monitor.py --all            # every enabled filter
```

Behaviour:
1. Load `filters.json` with plain `json.load` (do NOT import `gui.py`). Load
   `sources_config.yaml` via `src.sources.config.load_sources_config()`.
2. Resolve the named filter, or every `enabled: true` filter.
3. Instantiate `SourceOrchestrator(config)` once; per filter call
   `orchestrator.search(filter_dict, source_selection=filter.get("source_selection"),
   max_results=args.max or 200)`.
4. Print progress to stderr. Emit each deduped record as one JSON object per line to stdout
   using `record.to_dict()`. If `--json PATH`, also write the whole list to that file.
5. `--download-dir`: download each record's `pdf_url` (when present) with `requests` to
   `<dir>/<arxiv_id>_<slug>.pdf`, skipping files that already exist. Default: metadata only.
6. `--dry-run`: identical but no downloads and no `--json` write.

## Change 4 — `filters.json`: replace the `EPmodel` filter

Replace the existing `"EPmodel"` filter with one named **`"Agent Simulation"`**, `enabled: true`,
`days_back: 14`, `source_selection: {"all": false, "selected": ["arxiv", "psyarxiv", "socarxiv"]}`.
Keep the rest of the file untouched.

Encode these six facets as `text_groups` (groups OR-joined; within a group, `both` terms are
OR-joined and AND-ed against any `abstract` terms). Suggested groups — keep the anchor terms,
tune phrasing if needed:

1. ARCH (agent architecture): `both: "generative agents, multi-agent simulation, agent-based model, interactive simulacra"`
2. FIDELITY (validity): `both: "algorithmic fidelity, silicon samples"`
3. TRAIT (persona/emotion): `abstract: "large language model"`, `both: "persona, personality, emotion"`
4. ADAPT (self-adapting): `abstract: "language model"`, `both: "self-adapting, self-improving"`
5. DYNAMICS (misalignment): `both: "emergent misalignment"`
6. MECH (computational behaviour): `abstract: "reinforcement learning"`, `both: "addiction"`

Leave `authors`, `institution`, `paper_type`, `version`, `published`, `license`, `species` at
their `(any)`/empty defaults.

## Tests

- `tests/test_query_builder.py`: add `build_arxiv_query` cases — phrase quoting, OR-within-field,
  AND-across-fields, group OR-join, `submittedDate:[... TO ...]` format, no-text → date-only.
- `tests/test_adapters.py` (or a new `tests/test_arxiv.py`): **mock the HTTP layer** (no live
  network — read how `tests/test_adapters.py` / `test_orchestrator.py` mock `requests`/`urlopen`
  and follow that). Assert `normalize()` yields `canonical_id == "arxiv:<id>"` with version
  stripped, correct `pdf_url`, `is_preprint=True`, `subjects` populated, and that a withdrawn
  entry is dropped from `search()` output.

## Acceptance / verification

1. `/opt/homebrew/bin/pytest tests/ -v` — all pass (existing + new).
2. Imports cleanly: `python -c "from src.sources.arxiv import ArxivAdapter; print(ArxivAdapter.source_name)"` → `arxiv`.
3. Live smoke test: `python agents/monitor.py --filter "Agent Simulation" --dry-run --max 50`
   prints arXiv records (you should see LLM-agent / generative-agents / simulation papers with
   `"source": "arxiv"`), no crash, no 429 (the adapter must sleep on Retry-After).

## Don'ts

- Don't touch `gui.py` or change any existing GUI behaviour.
- Don't add third-party dependencies — use stdlib `urllib` or the already-present `requests`;
  parse Atom XML with `xml.etree.ElementTree` (no `feedparser`).
- Don't remove/rename existing adapters or the old `agents/search_agent.py` path — leave them.
- No secrets/keys anywhere; arXiv needs none.
- Respect rate limits (≥3 s between requests; back off on 429).
