"""
Batch C — replace datetime.utcnow() with datetime.now(timezone.utc) across the
retrieval layer and cache (P5 deprecation class fix).

Tests named test_c_* verify:
  - no utcnow() call remains anywhere under src/
  - the suite passes without DeprecationWarning from within src/ modules
"""
import ast
import subprocess
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"


def _collect_utcnow_calls(root: Path) -> list[tuple[Path, int]]:
    """Return (file, line_number) for every ast.Attribute(attr='utcnow') call in root."""
    hits = []
    for py_file in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "utcnow"
            ):
                hits.append((py_file, node.lineno))
    return hits


def test_c_no_utcnow_remains():
    """
    AST scan: no datetime.utcnow() call exists anywhere under src/.
    A substring scan would miss aliases; the AST walks actual call nodes.
    """
    hits = _collect_utcnow_calls(SRC)
    if hits:
        lines = "\n".join(f"  {p.relative_to(ROOT)}:{ln}" for p, ln in hits)
        raise AssertionError(
            f"datetime.utcnow() still present in src/ (use datetime.now(timezone.utc)):\n{lines}"
        )


def test_c_src_imports_cleanly_without_deprecation_warning():
    """
    Import the affected modules in a subprocess with -W error::DeprecationWarning so
    that any surviving utcnow() call (or other datetime deprecation) causes a hard failure.
    """
    affected = [
        "src.sources.arxiv",
        "src.sources.biorxiv_medrxiv",
        "src.sources.cache",
        "src.sources.europepmc",
        "src.sources.psyarxiv",
        "src.sources.socarxiv",
    ]
    code = "; ".join(f"import {m}" for m in affected)
    result = subprocess.run(
        [
            sys.executable,
            "-W", "error::DeprecationWarning",
            "-c", code,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"DeprecationWarning raised on import of src modules:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
