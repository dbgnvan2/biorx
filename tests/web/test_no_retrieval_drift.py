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
# Each advance is a W1.a-flagged exception documented here and in the commit message.
BASELINE = "e734ef4"

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
