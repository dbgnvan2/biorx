"""
Guards the web app's central constraint: the retrieval pipeline is wrapped, not
modified.

Spec: docs/implementation_plan_2026-09-15.md W1.a — "The orchestrator, dedup,
query builder, and adapters stay exactly as they are. Wrap them, don't modify
them, unless a change is strictly required to expose a route — and if so, flag
it explicitly."

The one change that WAS required, and was flagged, is src/db.py: it held a
single connection shared across threads on the argument that writes are always
serial, which a web app makes false. db.py is persistence, not retrieval, and is
deliberately not in the protected set below.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

ROOT = Path(__file__).parent.parent.parent

# The last commit that intentionally touched retrieval-layer files.
# Updated from a3769ef → ad2818e (Batch H: User-Agent hygiene + Unpaywall guard)
# Updated from ad2818e → 531b24f (test-qa fix: orch.warnings added to orchestrator.py
#   to surface startup conditions to callers — an interface addition, not retrieval logic)
# Updated from 531b24f → fa4665c (chdp F1 fix: ArxivAdapter.sources_config threads the
#   loaded config so contact_email from YAML reaches arXiv requests, matching how
#   Crossref/Unpaywall/OpenAlex work; orchestrator.py passes self.config to ArxivAdapter)
# Updated from fa4665c → a75924f (chdp F1-F3 completion: _crossref_abstract passes
#   get_crossref_user_agent(cfg) to CrossrefAdapter; orchestrator warns when Crossref/
#   arXiv active with no contact email; placeholder defaults removed from unpaywall.py
#   and crossref.py — "research@example.com"/"ResearchTool/1.0" replaced with ""/"biorx/1.0")
# Updated from a75924f → febd2fd (chdp F1-F2: EuropePmcAdapter, PsyArxivAdapter,
#   SocArxivAdapter now accept sources_config; orchestrator passes self.config;
#   polite-pool warning fires unconditionally with all consumers named)
# Updated from febd2fd → 128cef6 (chdp F1-F2 sibling: PubMedAdapter was missing
#   sources_config pass-through; warning updated to include PubMed in consumer list)
# Updated from 128cef6 → a55b83e (chdp F1 final: BiorxivMedrxivAdapter threads
#   sources_config to BioRxivAPI.user_agent; orchestrator passes self.config; all
#   seven search-source adapters now send polite_user_agent(sources_config) — P5 class closed)
# Updated from a55b83e → b0a7ca8 (batch D: arXiv wildcard strip, version carry,
#   5xx retry, M3 author clause removed; schema.py adds arxiv_version field;
#   query_builder.py drops normalize_authors — W1.a required changes)
# Updated from b0a7ca8 → b973db1 (batch C: datetime.utcnow() → datetime.now(timezone.utc)
#   across all adapters; P5 deprecation class fix; no logic change — W1.a required)
# Updated from b973db1 → 9071d92 (batch G: with_retry wrapped in adapter search() calls;
#   orchestrator.py gains `if fetched==0: raise` to surface empty-page source failure —
#   logic addition required to distinguish "source yielded nothing" from "source errored
#   before yielding"; base.py unchanged in substance — W1.a required)
# Updated from 9071d92 → 6d49624 (batch H: with_retry wrapped in all sibling HTTP calls
#   — get_total, get_by_id, fetch_abstract_from_fulltext — in europepmc.py, psyarxiv.py,
#   socarxiv.py; closes P5 class — W1.a required)
# Updated from 6d49624 → a2607f8 (batch I: _search_source gains on_status kwarg; mid-
#   pagination SourceUnavailableError now emits "partial — skipped" marker so monitor.py
#   exits 2 on truncated results — P2/P19 fix; interface addition, not retrieval logic
#   change — W1.a required)
# Updated from a2607f8 → cb3d6f0 (batch I follow-up: FAILURE_STATUS_MARKER constant
#   added to orchestrator.py; all 4 on_status failure emission sites now use it; message
#   format normalised to "<label> — skipped (<qualifier>)" so monitor.py split() extracts
#   the label cleanly — P19 single-source-of-truth fix; W1.a required)
# Updated from cb3d6f0 → 5b8ec2a (batch I QA gate follow-up: comment in orchestrator.py
#   updated to reflect new "— skipped (unavailable)" format — cosmetic only; W1.a required)
# Updated from 5b8ec2a → 0428f66 (batch I QA gate round 2: advance past the cosmetic
#   comment fix; no retrieval logic change — W1.a required)
# Updated from 0428f66 → e734ef4 (plan 2026-09-18 C2/C3: search() gains optional
#   enrich_only and on_enrich_progress. Enrichment ran on every fetched paper and
#   its output never reached results; it is internal to search(), so it cannot be
#   narrowed from outside. Defaults keep the old behaviour — W1.a required)
# Updated from e734ef4 → 4694712 (issue 3, 2026-09-18: Crossref/Unpaywall enrich return
#   False on a failed lookup; _enrich counts and reports failures via WARNING,
#   on_status and on_enrich_problem. Silent debug-only failures hid outages (P2)
#   — W1.a required)
# Updated from 4694712 → 493288a (review 2026-09-28 batch 1: B1 per-source budget
#   and truncation reports, B2 empty dates, B3/M31 shared OSF adapter, B6 per-search
#   adapter instances, B7/M18 bioRxiv+medRxiv over the filter's dates, M19-M22
#   enrichment errors, unreadable records, outages raised, dedup surnames; the
#   Europe PMC pubTypeList fix. Plan docs/implementation_plan_2026-09-28_review_fixes.md
#   — W1.a required)
# Updated from 493288a → eb2302d (review 2026-09-28 batch 2, A1: arxiv.py gains a
#   working get_by_id (id_list) and crossref.py a record_for_doi, so the summary
#   route can look a paper up server-side instead of trusting the request body
#   — W1.a required)
# Updated from eb2302d → de3c124 (batch-2 gate finding 3: EuropePmcAdapter.get_by_id
#   queries a PMCID by field so pmcid: canonical ids can be looked up — W1.a required)
# Updated from de3c124 → dd0f496 (review batch 3, S2: orchestrator.search normalises the
#   filter, refuses an empty one, and reports source failures through
#   on_source_failure; one label map via config.source_label — W1.a required)
# Updated from dd0f496 → 26327ec (browser run 2026-09-29, docs/cycles/2026-09-29_browser-run.md:
#   F2 bioRxiv/medRxiv paging — the API sends 30 per call and the adapter stopped
#   after one, reading ~1% of the window silently; the adapter reports has_more and
#   the orchestrator trusts it. F4 Europe PMC/Crossref text through the new shared
#   src/sources/markup.py, now protected too. QA gate finding 1 — W1.a required)
# Updated from 26327ec → f854662 (owner's decision 2026-09-29: bioRxiv/medRxiv count
#   matches against Max results, bounded by a configured page limit — orchestrator
#   filters a filters_locally source per page; PubMed label; markup decodes entities
#   before removing tags — W1.a required)
# Updated from f854662 → 7acd226 (bioRxiv-budget QA gate F2: a filters_locally source
#   reports matches through on_progress and papers read through on_status — W1.a required)
# Updated from 7acd226 → 21d2b35 (production run 2026-09-29: the orchestrator counts matches
#   that repeat a paper already read, so papers read = match + do not + repeats +
#   unreadable, and warns when they do not add up — W1.a required)
# Updated from 21d2b35 → 7ea410b (plan 2026-09-29 T1.1: dedup keys a multi-word surname on its
#   last word in every branch, so Europe PMC "da Silva" and arXiv "Ana da Silva" merge
#   — W1.a required)
# Updated from 7ea410b → a92857b (plan 2026-09-29 tiers 2–3: unpaywall.py sends the contact
#   User-Agent (T2.3), config.py gains user_agent_with, query_builder.py's arXiv all:
#   note corrected (T3.6, docstring only) — W1.a required)
# Updated from a92857b → a9cfc35 (QA gate 2026-09-30 F1: the T1.1 surname key keeps lower-case
#   particles, so "da Silva" and "Silva" stay two authors — W1.a required)
# Updated from a9cfc35 → 2765a9f (decision D1, desktop app retired: comments in orchestrator.py,
#   query_builder.py and schema.py no longer name the GUI as a front end; no code
#   change — W1.a required)
# Updated from 2765a9f → 667e3de (search progress, 2026-10-01: _search_source announces each
#   page before requesting it, and the OSF adapter calls an optional on_activity hook per
#   title-term request, so a slow PsyArXiv search does not look hung — status text only,
#   no change to what is fetched or kept — W1.a required)
# Each advance is a W1.a-flagged exception documented here and in the commit message.
# Updated from 667e3de → 408deae (AND1–AND5, 2026-10-07: query_builder.py reads "a AND b"
#   inside a term via src/search_terms.and_parts — Europe PMC/PubMed and arXiv get both parts,
#   OSF gets the longest part; required to send the new operator to the sources, flagged in
#   docs/implementation_plan_2026-10-07_and_terms.md)
# Updated from 408deae → b384965 (TA1, 2026-10-07: _group_to_lucene sends Title-or-abstract terms
#   as TITLE_ABS: instead of bare terms, which matched full text and filled Max results with
#   records the local filter drops; flagged in docs/implementation_plan_2026-10-07_title_abs.md)
# Updated from b384965 → 203668f (BW2/BW3, 2026-10-07: biorxiv_medrxiv.py applies
#   src/sources/biorxiv_window.py — a range ending long ago is not read, a long recent range reads
#   only its newest days; flagged in docs/implementation_plan_2026-10-07_biorxiv_window.md)
# Updated from 203668f → 4ff7421 (DS1, 2026-10-07: orchestrator.fetched_status names a source's
#   papers that an earlier source already found — status text only, nothing fetched, merged or
#   kept changes; flagged in docs/implementation_plan_2026-10-07_duplicate_status.md)
# Updated from 4ff7421 → 29aca88 (DS gate F1–F3: counts returned through a per-call dict, own repeats
#   told apart from earlier sources, no second line for bioRxiv — status text only)
# Updated from 29aca88 → ee3e301 (TD1/TD3/TD6/TD7/TD8/TD9, 2026-10-07: query syntax quoted and
#   arXiv wildcards sent (query_builder.py), bioRxiv servers setting and per-search window
#   (biorxiv_medrxiv.py), public resolve_active_sources and re-send counting (orchestrator.py);
#   flagged in docs/implementation_plan_2026-10-07_gate_todos.md)
# Updated from ee3e301 → 155cc75 (HW1, 2026-10-07: a wildcard word is split on spaces and punctuation
#   in query_builder._term_clause; flagged in docs/implementation_plan_2026-10-07_hyphen_wildcard.md)
BASELINE = "155cc75"

# Everything W1.a names. Adapters are listed individually rather than by glob so
# that adding an adapter is a deliberate edit here, not a silent widening.
PROTECTED = [
    "src/sources/orchestrator.py",
    "src/sources/dedup.py",
    "src/sources/query_builder.py",
    "src/sources/schema.py",
    "src/sources/base.py",
    "src/sources/arxiv.py",
    "src/sources/europepmc.py",
    "src/sources/pubmed.py",
    "src/sources/psyarxiv.py",
    "src/sources/socarxiv.py",
    "src/sources/biorxiv_medrxiv.py",
    "src/sources/crossref.py",
    "src/sources/unpaywall.py",
    "src/sources/markup.py",
]


def _git(*args) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                            text=True, timeout=30)
    if result.returncode != 0:
        # A skip here would hide a bad BASELINE silently (P27/P35).
        pytest.fail(f"git command failed: {result.stderr.strip()}")
    return result.stdout


def test_the_protected_files_all_exist():
    """A path that no longer exists would make the diff below vacuously empty."""
    missing = [p for p in PROTECTED if not (ROOT / p).exists()]
    assert missing == [], f"protected paths are missing: {missing}"


def test_retrieval_modules_unchanged_since_the_web_work_began():
    """
    The whole premise of the web app: it wraps the pipeline. If this fails, the
    change may still be right — but it needs saying out loud, not absorbing.
    """
    changed = _git("diff", "--name-only", f"{BASELINE}..HEAD", "--", *PROTECTED)
    touched = [line for line in changed.splitlines() if line.strip()]
    assert touched == [], (
        "the web app modified retrieval code it was supposed to wrap: "
        f"{touched}. If the change is genuinely required, say so in the commit "
        "and update this test deliberately."
    )


def test_the_baseline_commit_is_reachable():
    """Guard-the-guard: a bad baseline makes the diff empty for the wrong reason.
    Uses subprocess directly (not _git) so an unreachable BASELINE fails, not skips."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", BASELINE],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and result.stderr == "", (
        f"BASELINE {BASELINE!r} is not reachable: {result.stderr.strip()}"
    )
    assert result.stdout.strip(), f"BASELINE {BASELINE!r} returned an empty subject"


def test_the_guard_would_notice_a_change():
    """
    Proves the -- filter in the drift check works by using the same range
    (BASELINE..HEAD) and a file proven to have changed in that range.
    Skips when BASELINE == HEAD (empty range; nothing to guard against).
    """
    all_changed = _git("diff", "--name-only", f"{BASELINE}..HEAD").splitlines()
    non_protected = [f for f in all_changed if f.strip() and f.strip() not in PROTECTED]
    if not non_protected:
        pytest.skip("no commits since baseline — empty range, guard not needed")
    probe = non_protected[0].strip()
    result = _git("diff", "--name-only", f"{BASELINE}..HEAD", "--", probe)
    assert probe in result.splitlines(), (
        f"git diff with -- filter did not return {probe!r} even though it "
        "appeared in the unfiltered diff — the filter mechanism is broken"
    )
