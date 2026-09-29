"""
Tests for requirements-web.txt — the container's dependency set.

Spec: docs/implementation_plan_2026-09-15.md#2.6, W7.a
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
REQ = REPO_ROOT / "requirements-web.txt"


def _pins():
    for line in REQ.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, version = line.partition("==")
        yield re.sub(r"\[.*\]$", "", name), sep, version


def test_web_requirements_exist():
    assert REQ.exists()


def test_pyqt6_is_never_installed_in_the_container():
    """
    The web image must not pull the desktop GUI. A stray PyQt6 line would add a
    display dependency to a headless container.

    Matches dependency LINES, not the file text: the first version of this test
    grepped the whole file and failed on the comment above explaining the check
    (learnings P19's corollary — never match a bare substring against text that
    also contains prose).
    """
    named = {name.lower() for name, _, _ in _pins()}
    assert not any(n.startswith("pyqt") for n in named), sorted(named)


def test_every_dependency_is_pinned():
    """standards S7: an unpinned dependency silently changes on every build."""
    unpinned = [name for name, sep, _ in _pins() if sep != "=="]
    assert unpinned == [], f"unpinned: {unpinned}"


@pytest.mark.parametrize("name,module", [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("itsdangerous", "itsdangerous"),
    ("cryptography", "cryptography"),
    ("anthropic", "anthropic"),
    ("requests", "requests"),
    ("PyYAML", "yaml"),
    ("pdfplumber", "pdfplumber"),
    ("urllib3", "urllib3"),       # src/safe_fetch.py imports it directly
    ("certifi", "certifi"),
    ("fpdf2", "fpdf"),           # src/summary_pdf.py
    ("python-dotenv", "dotenv"),  # src/env_file.py
])
def test_each_pinned_package_is_real_and_importable(name, module):
    """
    Catches an invented package name or a version that does not exist — a pin
    written from memory rather than from something that installed.
    """
    pinned = {n for n, _, _ in _pins()}
    assert name in pinned, f"{name} missing from requirements-web.txt"
    # import_module, not importorskip: a pinned dependency that is missing is
    # a failure, not a skip (review M38).
    import importlib
    importlib.import_module(module)


def test_pins_name_a_concrete_version():
    """
    Every pin must carry a real, complete version. Catches a pin written from
    memory as a bare name, a range, or a truncated number. (That the pins equal
    what is installed is test_b10_pins_match_installed.)
    """
    import re as _re

    for name, sep, version in _pins():
        assert sep == "==", f"{name} is not pinned"
        assert _re.fullmatch(r"\d+(\.\d+)+", version), \
            f"{name} has a suspicious version {version!r}"



# ── B10: pins are what is installed, and nothing known-vulnerable ────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B10

TEST_REQ = REPO_ROOT / "requirements-test.txt"
LOCK = REPO_ROOT / "requirements-web.lock"


def _pinned(path):
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line and "==" in line and not line.startswith("--"):
            name, _, version = line.partition("==")
            name = re.sub(r"\[.*\]$", "", name).strip()
            # PEP 503 names, as the lock writes them (pdfminer.six -> pdfminer-six).
            yield re.sub(r"[-_.]+", "-", name).lower(), version.split(";")[0].split()[0].strip(" \\")


def test_b10_pins_match_installed():
    """The file said its pins were "verified against this machine" while the
    venv ran different versions (urllib3 2.6.3 vs 2.2.2). Every run of the
    suite — here and in CI, which installs from these files — checks it."""
    from importlib.metadata import version
    wrong = {name: (pin, version(name))
             for path in (REQ, TEST_REQ) for name, pin in _pinned(path)
             if version(name) != pin}
    assert wrong == {}, wrong


def test_b10_test_tools_stay_out_of_the_image():
    web = {n.lower() for n, _ in _pinned(REQ)}
    assert not {"pytest", "httpx"} & web
    assert {"pytest", "httpx"} <= {n.lower() for n, _ in _pinned(TEST_REQ)}


def test_b10_pdfminer_patched():
    """pdfminer.six before 20251107 loads pickled CMaps from a path a PDF names
    (CVE-2025-64512); the server parses PDFs from third parties."""
    from importlib.metadata import version
    assert int(version("pdfminer.six")) >= 20251107
    locked = dict(_pinned(LOCK))
    assert int(locked["pdfminer-six"]) >= 20251107


def test_b10_lock_matches_the_pins_and_is_hashed():
    """The image installs requirements-web.lock with --require-hashes; it must
    pin the same versions as requirements-web.txt, and carry a hash for each."""
    locked = {n.lower(): v for n, v in _pinned(LOCK)}
    for name, pin in _pinned(REQ):
        assert locked.get(name.lower()) == pin, (name, pin, locked.get(name.lower()))
    text = LOCK.read_text()
    entries = [b for b in re.split(r"\n(?=[A-Za-z])", text) if "==" in b.split("\n")[0]]
    assert entries and all("--hash=sha256:" in b for b in entries)


def test_b10_dockerfile_regen_command_matches_the_lock_header():
    """Gate 6 note 3: the comment's command makes the same lock the header records."""
    root = Path(__file__).parent.parent.parent
    comment = re.search(r"#\s+(pip-compile [^\n]+)", (root / "Dockerfile").read_text()).group(1)
    header = re.search(r"#\s+(pip-compile [^\n]+)", (root / "requirements-web.lock").read_text()).group(1)
    flags = lambda cmd: {f.split("=")[0] for f in cmd.split() if f.startswith("--")}
    # --no-index in the header is written by pip-compile itself for
    # --no-emit-index-url; --output-file is spelt -o in the comment.
    assert flags(comment) == flags(header) - {"--no-index", "--output-file"}
