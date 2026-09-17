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


def _root_md_files():
    """Root-level Markdown files only.

    docs/cycles/ holds historical gate files that quote personal addresses for
    audit purposes — those are excluded by not recursing into subdirectories.
    """
    yield from ROOT.glob("*.md")


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


def test_h_no_personal_address_in_root_markdown():
    """
    Root-level .md files (README, TODO, specs) must not contain a personal
    address. docs/cycles/ is excluded — gate files quote addresses for audit.
    Scans raw text (no AST) because Markdown has no comment syntax.
    """
    found = []
    for path in _root_md_files():
        if not path.exists():
            continue
        for addr in EMAIL.findall(path.read_text(encoding="utf-8")):
            if not ALLOWED_DOMAIN.search(addr):
                found.append(f"{path.relative_to(ROOT)}: {addr}")
    assert found == []


def test_h_root_md_scan_would_catch_one(tmp_path):
    """Guard-the-guard: the raw Markdown scanner finds an address in plain text."""
    sample = tmp_path / "TASK.md"
    sample.write_text("Send feedback to real@person.example.io and nothing else.\n")
    hits = EMAIL.findall(sample.read_text())
    non_example = [a for a in hits if not ALLOWED_DOMAIN.search(a)]
    assert non_example == ["real@person.example.io"]


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
    """Env path: BIORX_CONTACT_EMAIL reaches the arXiv User-Agent header."""
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


def test_h_arxiv_request_carries_config_contact_address(monkeypatch):
    """Config path: contact_email from sources_config reaches arXiv User-Agent.

    A user who sets contact_email in sources_config.yaml but not BIORX_CONTACT_EMAIL
    must still get the mailto header on arXiv requests (P5: all four polite-pool
    consumers must be wired, not just Crossref/Unpaywall).
    """
    from src.sources import arxiv

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
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
    adapter = arxiv.ArxivAdapter(
        min_request_interval=0,
        sources_config={"contact_email": "cfg@example.org"},
    )
    adapter.search("all:test", page=1, page_size=1, filter_dict={})
    assert sent["headers"]["User-Agent"] == "biorx/1.0 (mailto:cfg@example.org)"


def test_h_crossref_abstract_carries_config_contact_address(monkeypatch):
    """
    _crossref_abstract passes the loaded sources_config to CrossrefAdapter so
    the abstract-recovery Crossref path sends a polite mailto UA (F1/P5).

    Tests the boundary the adapter actually uses, not a mock of the mock.
    """
    from src import paper_meta as pm
    from src.sources import crossref as crossref_mod

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    captured = {}

    class _FakeAdapter:
        def __init__(self, user_agent="biorx/1.0", timeout=15):
            captured["user_agent"] = user_agent

        def enrich(self, record):
            pass

    monkeypatch.setattr(crossref_mod, "CrossrefAdapter", _FakeAdapter)
    # Patch load_sources_config in paper_meta (the local import) to return config email.
    # _crossref_abstract does `from .sources.config import ... load_sources_config`
    # at call time; patch the config module directly.
    from src.sources import config as sources_config_mod
    monkeypatch.setattr(sources_config_mod, "load_sources_config",
                        lambda: {"contact_email": "cfg@example.org"})

    pm._crossref_abstract("10.1234/test")
    assert captured["user_agent"] == "biorx/1.0 (mailto:cfg@example.org)", (
        "_crossref_abstract must pass the config-resolved user_agent to CrossrefAdapter; "
        f"got: {captured.get('user_agent')}"
    )


def test_h_orchestrator_warns_when_crossref_active_but_no_email(monkeypatch):
    """
    When Crossref is enabled and no contact email is set, orchestrator.warnings
    includes a message so the caller can surface the degraded mode (F2/P2/P5).
    """
    from src.sources.orchestrator import SourceOrchestrator

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    cfg = {"publication_sources": {"crossref": {"enabled": True}}}
    orch = SourceOrchestrator(cfg)
    assert any("BIORX_CONTACT_EMAIL" in w and "Crossref" in w for w in orch.warnings), (
        "expected a Crossref/BIORX_CONTACT_EMAIL warning in orch.warnings; "
        f"got: {orch.warnings}"
    )


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

    # SummarizationAgent binds src.db.Database directly (not gui_module.Database),
    # so we must patch it here or MainWindow.__init__ reaches the real production DB.
    # load_filters / FILTERS_PATH reads the real filters.json — patch those too (P34).
    with patch.object(gui_module, "SourceOrchestrator", return_value=fake_orch), \
         patch.object(gui_module, "load_sources_config", return_value={}), \
         patch.object(gui_module, "Database", return_value=MagicMock()), \
         patch.object(gui_module, "SummarizationAgent", return_value=MagicMock()), \
         patch.object(gui_module, "load_filters", return_value=[]), \
         patch.object(gui_module.MainWindow, "_show_startup_warnings", fake_show):

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        win = gui_module.MainWindow()

    assert captured == fake_orch.warnings, (
        "MainWindow.__init__ did not call _show_startup_warnings with orchestrator.warnings"
    )


def test_h_show_startup_warnings_renders_to_status_bar_and_logger(caplog):
    """
    _show_startup_warnings body: showMessage receives f"⚠ {msg}" with timeout 0,
    and logger.warning is called (P19 — patching away the method in the prior test
    proves only the call site, not the rendering).

    Tests the method directly via __new__ so MainWindow.__init__ is never invoked.
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    from unittest.mock import MagicMock, patch
    import gui as gui_module

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    win = gui_module.MainWindow.__new__(gui_module.MainWindow)

    mock_bar = MagicMock()
    with patch.object(win, "statusBar", return_value=mock_bar), \
         caplog.at_level(logging.WARNING):
        win._show_startup_warnings(["Open-access lookup is off."])

    mock_bar.showMessage.assert_called_once_with("⚠ Open-access lookup is off.", 0)
    assert any(
        "Startup" in r.getMessage() and "Open-access" in r.getMessage()
        for r in caplog.records
    )


def test_h_europepmc_carries_config_contact_address(monkeypatch):
    """Config path: contact_email from sources_config reaches EuropePMC User-Agent (F1/P5)."""
    from src.sources.europepmc import EuropePmcAdapter

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    adapter = EuropePmcAdapter(sources_config={"contact_email": "cfg@example.org"})
    assert adapter.session.headers["User-Agent"] == "biorx/1.0 (mailto:cfg@example.org)", (
        f"EuropePmcAdapter must use config contact_email in UA; got: {adapter.session.headers.get('User-Agent')}"
    )


def test_h_psyarxiv_carries_config_contact_address(monkeypatch):
    """Config path: contact_email from sources_config reaches PsyArXiv User-Agent (F1/P5)."""
    from src.sources.psyarxiv import PsyArxivAdapter

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    adapter = PsyArxivAdapter(sources_config={"contact_email": "cfg@example.org"})
    assert adapter.session.headers["User-Agent"] == "biorx/1.0 (mailto:cfg@example.org)", (
        f"PsyArxivAdapter must use config contact_email in UA; got: {adapter.session.headers.get('User-Agent')}"
    )


def test_h_socarxiv_carries_config_contact_address(monkeypatch):
    """Config path: contact_email from sources_config reaches SocArXiv User-Agent (F1/P5)."""
    from src.sources.socarxiv import SocArxivAdapter

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    adapter = SocArxivAdapter(sources_config={"contact_email": "cfg@example.org"})
    assert adapter.session.headers["User-Agent"] == "biorx/1.0 (mailto:cfg@example.org)", (
        f"SocArxivAdapter must use config contact_email in UA; got: {adapter.session.headers.get('User-Agent')}"
    )


def test_h_pubmed_carries_config_contact_address(monkeypatch):
    """PubMedAdapter subclasses EuropePmcAdapter; sources_config must reach it too (F1/P5)."""
    from src.sources.pubmed import PubMedAdapter

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    adapter = PubMedAdapter(sources_config={"contact_email": "cfg@example.org"})
    assert adapter.session.headers["User-Agent"] == "biorx/1.0 (mailto:cfg@example.org)", (
        f"PubMedAdapter must use config contact_email in UA; got: {adapter.session.headers.get('User-Agent')}"
    )


def test_h_orchestrator_warns_about_openalex_when_no_email(monkeypatch):
    """
    The no-email startup warning names OpenAlex so users know the silent degradation
    extends beyond sources they explicitly enabled (F2/P5).

    OpenAlex runs unconditionally from paper_meta.py; the warning fires whenever
    no contact email is set regardless of which optional sources are enabled.
    """
    from src.sources.orchestrator import SourceOrchestrator

    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    orch = SourceOrchestrator({})
    assert any("OpenAlex" in w for w in orch.warnings), (
        "expected an OpenAlex mention in orch.warnings when no email is set; "
        f"got: {orch.warnings}"
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
