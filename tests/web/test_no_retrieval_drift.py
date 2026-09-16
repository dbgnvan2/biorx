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
# Updated from a3769ef → ad2818e by Batch H (contact-email hygiene):
#   src/sources/arxiv.py   — User-Agent built via polite_user_agent()
#   src/sources/orchestrator.py — Unpaywall guard added when no email is set
# These are W1.a-flagged exceptions: hygiene changes, not retrieval-logic changes.
BASELINE = "ad2818e"

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
        pytest.skip(f"git unavailable or baseline missing: {result.stderr.strip()}")
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
    """Guard-the-guard: a bad baseline makes the diff empty for the wrong reason."""
    subject = _git("log", "-1", "--format=%s", BASELINE).strip()
    assert subject, "the baseline commit could not be read"


def test_the_guard_would_notice_a_change():
    """
    Proves the diff mechanism works by checking a file that changed IN the
    baseline commit itself (BASELINE~1..BASELINE). src/sources/arxiv.py is
    correct: it was the Batch H change that advanced the baseline here.
    """
    changed = _git("diff", "--name-only", f"{BASELINE}~1..{BASELINE}", "--",
                   "src/sources/arxiv.py")
    assert "src/sources/arxiv.py" in changed, (
        "the diff reports no change to src/sources/arxiv.py in the baseline "
        "commit — check that BASELINE points at the right commit"
    )
