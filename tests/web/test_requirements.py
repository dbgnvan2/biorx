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
    # CI installs only this file and then runs `pytest`: without the pin every
    # CI run failed with "pytest: command not found" (all runs to 2026-09-17).
    ("pytest", "pytest"),
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
    pytest.importorskip(module)


def test_pins_name_a_concrete_version():
    """
    Every pin must carry a real, complete version. Catches a pin written from
    memory as a bare name, a range, or a truncated number.

    NOT asserted here: that the pins equal what is installed on this machine.
    There are two interpreters in play — the documented test command runs
    Python 3.11 and the app runs 3.12, with different installed versions — so
    an equality check would fail for a reason that says nothing about the file.
    The container installs from this file, and CI on a blank machine is what
    proves the pins resolve.
    """
    import re as _re

    for name, sep, version in _pins():
        assert sep == "==", f"{name} is not pinned"
        assert _re.fullmatch(r"\d+(\.\d+)+", version), \
            f"{name} has a suspicious version {version!r}"
