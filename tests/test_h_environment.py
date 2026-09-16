"""
Batch H — environment and CI.

Spec: docs/implementation_plan_2026-09-16_backlog.md#batch-h (as amended in §6)

Each user now runs their own copy (docs/spec_make_it_great.md D1), so a contact
address baked into the repository would be sent to Crossref, Unpaywall, arXiv
and OpenAlex on behalf of every colleague. The address comes from the user's
environment or their own config, and nothing personal ships in the source.
"""
import ast
import logging
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.sources import config as sources_config  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Placeholder domains reserved for documentation (RFC 2606) are allowed.
ALLOWED_DOMAIN = re.compile(r"@example\.(com|org|net)$")

SCANNED_DIRS = ["src", "web", "agents"]


def _python_files():
    for d in SCANNED_DIRS:
        yield from (ROOT / d).rglob("*.py")
    yield ROOT / "gui.py"


def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_h_no_personal_address_in_source():
    """
    Parses string constants (not raw text), so an address in a comment that
    explains this rule does not trip it, and one inside an f-string or a dict
    literal does (learnings P19 corollary).
    """
    found = []
    for path in _python_files():
        if not path.exists():
            continue
        for lineno, value in _string_constants(path):
            for addr in EMAIL.findall(value):
                if not ALLOWED_DOMAIN.search(addr):
                    found.append(f"{path.relative_to(ROOT)}:{lineno}: {addr}")
    assert found == []


def test_h_no_personal_address_in_shipped_yaml():
    yaml = pytest.importorskip("yaml")
    found = []

    def walk(value, where):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{where}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{where}[{i}]")
        elif isinstance(value, str):
            for addr in EMAIL.findall(value):
                if not ALLOWED_DOMAIN.search(addr):
                    found.append(f"{where}: {addr}")

    for name in ("sources_config.yaml", "llm_config.yaml"):
        walk(yaml.safe_load((ROOT / name).read_text(encoding="utf-8")), name)
    assert found == []


def test_h_the_address_scan_would_catch_one(tmp_path):
    """Guard-the-guard: the scanner finds an address placed where code keeps it."""
    sample = tmp_path / "sample.py"
    sample.write_text('# someone@realmail.test in a comment\nUA = {"u": f"x (mailto:someone@realmail.io)"}\n')
    hits = [a for _, v in _string_constants(sample) for a in EMAIL.findall(v)]
    assert hits == ["someone@realmail.io"]


# ── contact address resolution ───────────────────────────────────────────────

def test_h_contact_email_env_wins_over_config(monkeypatch):
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "env@example.org")
    cfg = {"contact_email": "cfg@example.org"}
    assert sources_config.get_contact_email(cfg) == "env@example.org"


def test_h_contact_email_from_config_when_no_env(monkeypatch):
    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    assert sources_config.get_contact_email({"contact_email": "cfg@example.org"}) == "cfg@example.org"


def test_h_legacy_unpaywall_email_key_still_read(monkeypatch):
    """A user's existing sources_config.yaml keeps working."""
    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    assert sources_config.get_contact_email({"unpaywall_email": "old@example.org"}) == "old@example.org"


def test_h_no_contact_email_is_empty_not_a_placeholder(monkeypatch):
    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    assert sources_config.get_contact_email({}) == ""


def test_h_user_agent_has_mailto_only_when_an_address_is_set(monkeypatch):
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "env@example.org")
    assert sources_config.polite_user_agent({}) == "biorx/1.0 (mailto:env@example.org)"
    monkeypatch.delenv("BIORX_CONTACT_EMAIL")
    assert sources_config.polite_user_agent({}) == "biorx/1.0"


def test_h_crossref_user_agent_uses_the_contact_address(monkeypatch):
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "env@example.org")
    assert sources_config.get_crossref_user_agent({}) == "biorx/1.0 (mailto:env@example.org)"


def test_h_arxiv_request_carries_the_contact_address(monkeypatch):
    """The boundary: the header the arXiv adapter actually sends."""
    from src.sources import arxiv

    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "env@example.org")
    sent = {}

    class _Resp:
        status_code = 200
        text = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        content = text.encode()
        headers = {}

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, headers=None, timeout=None):
        sent["headers"] = headers
        return _Resp()

    monkeypatch.setattr(arxiv.requests, "get", fake_get)
    adapter = arxiv.ArxivAdapter(min_request_interval=0)
    adapter.search("all:test", page=1, page_size=1, filter_dict={})
    assert sent["headers"]["User-Agent"] == "biorx/1.0 (mailto:env@example.org)"


def test_h_no_contact_email_skips_unpaywall_and_says_so(monkeypatch, caplog):
    """
    Unpaywall requires an address. With none, the enricher is not registered and
    the log says why — not a silent drop, and not a fake address (learnings P2).
    """
    from src.sources.orchestrator import SourceOrchestrator

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    cfg = {"publication_sources": {"unpaywall": {"enabled": True}}}
    with caplog.at_level(logging.WARNING):
        orch = SourceOrchestrator(cfg)
    assert orch._unpaywall is None
    assert any("Unpaywall" in r.getMessage() and "BIORX_CONTACT_EMAIL" in r.getMessage()
               for r in caplog.records)


def test_h_no_contact_email_populates_warnings_list(monkeypatch):
    """
    The warning is stored on orch.warnings so callers (GUI, web app) can surface
    it to the user, not just to the log (learnings P2/P3).
    """
    from src.sources.orchestrator import SourceOrchestrator

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    cfg = {"publication_sources": {"unpaywall": {"enabled": True}}}
    orch = SourceOrchestrator(cfg)
    assert any("BIORX_CONTACT_EMAIL" in w for w in orch.warnings)


def test_h_contact_email_registers_unpaywall_with_it(monkeypatch):
    from src.sources.orchestrator import SourceOrchestrator

    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "env@example.org")
    cfg = {"publication_sources": {"unpaywall": {"enabled": True}}}
    orch = SourceOrchestrator(cfg)
    assert orch._unpaywall is not None and orch._unpaywall.email == "env@example.org"
    assert orch.warnings == []


# ── GUI wiring (P21/P25) ─────────────────────────────────────────────────────

def test_h_gui_calls_show_startup_warnings_with_orchestrator_warnings(monkeypatch):
    """
    MainWindow.__init__ passes orch.warnings to _show_startup_warnings (P21/P25).

    Tests the call, not a copy of the implementation: patching _show_startup_warnings
    and asserting it is called with the right argument means deleting the call in
    __init__ will make this test red (mutation-provable per P27).

    Requires PyQt6; skipped in CI where requirements-web.txt omits it (GUI is
    desktop-only; integration-only per testing rules).
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

    from unittest.mock import MagicMock, patch

    fake_orch = MagicMock()
    fake_orch.warnings = ["Open-access lookup is off: no contact email."]
    fake_orch.get_enabled_sources.return_value = []

    import gui as gui_module
    captured = []

    def fake_show(self, warnings):
        captured.extend(warnings)

    with patch.object(gui_module, "SourceOrchestrator", return_value=fake_orch), \
         patch.object(gui_module, "load_sources_config", return_value={}), \
         patch.object(gui_module, "Database", return_value=MagicMock()), \
         patch.object(gui_module.MainWindow, "_show_startup_warnings", fake_show):

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        win = gui_module.MainWindow()

    assert captured == fake_orch.warnings, (
        "MainWindow.__init__ did not call _show_startup_warnings with orchestrator.warnings"
    )


# ── platform ─────────────────────────────────────────────────────────────────

def test_h_sqlite_has_fts5():
    """
    V3 in docs/implementation_plan_2026-09-16_make_it_great.md: library search
    (spec G5.3) needs FTS5 on every platform CI runs, Windows included.
    """
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
        con.execute("INSERT INTO t(body) VALUES ('cortisol response')")
        rows = con.execute("SELECT body FROM t WHERE t MATCH 'cortisol'").fetchall()
    finally:
        con.close()
    assert rows == [("cortisol response",)]


def test_h_runs_on_the_supported_python():
    """Python 3.12 is what the install scripts pin (plan PD4, backlog §6).

    Skipped outside CI because the dev machine may use a different interpreter
    (e.g. Homebrew Python 3.11 for the test runner). CI always sets CI=true.
    """
    import os
    if not os.environ.get("CI"):
        pytest.skip("platform-version check runs in CI where Python 3.12 is installed")
    assert sys.version_info[:2] >= (3, 12)
