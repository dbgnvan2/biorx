"""
Tests for the single-page client.

Spec: docs/implementation_plan_2026-09-15.md#2.2, W5.a, W5.b

These check the two contracts that can drift silently between web/static/app.js
and the rest of the app (learnings P19/P25):

  * every element id the JS reaches for exists in index.html, so a renamed id
    breaks the build rather than silently disabling a control;
  * every API path the JS calls exists in the app's route table, so a renamed
    endpoint fails here rather than in the browser.

The behaviour of the controls themselves was verified by driving the real page
in a browser against a live server; that is recorded in the README's manual
checklist rather than simulated here.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

STATIC = Path(__file__).parent.parent.parent / "web" / "static"
INDEX = STATIC / "index.html"
APP_JS = STATIC / "app.js"
CSS = STATIC / "styles.css"


def _element_ids_in_html() -> set:
    return set(re.findall(r'id="([^"]+)"', INDEX.read_text()))


def _ids_used_by_js() -> set:
    """Ids the client looks up, via its $() helper or getElementById."""
    source = APP_JS.read_text()
    return set(re.findall(r'\$\("([^"]+)"\)', source)) | set(
        re.findall(r'getElementById\("([^"]+)"\)', source)
    )


def _api_paths_called_by_js() -> set:
    """Literal API paths the client calls, with parameters normalised."""
    source = APP_JS.read_text()
    paths = set()
    for raw in re.findall(r'api\(\s*"[A-Z]+"\s*,\s*[`"]([^`"]+)[`"]', source):
        path = raw.split("?")[0]
        path = re.sub(r"\$\{[^}]+\}", "{param}", path)
        paths.add(path)
    return paths


# ── Serving ───────────────────────────────────────────────────────────────────

def test_index_is_served_at_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>BioRx</title>" in r.text


@pytest.mark.parametrize("asset", ["/static/app.js", "/static/styles.css"])
def test_static_assets_are_served(client, asset):
    assert client.get(asset).status_code == 200


def test_the_page_references_only_local_assets(client):
    """
    No build step and no CDN: one deployable container that works offline
    (W5.a). An external script or stylesheet would also be a supply-chain
    dependency on every page load.
    """
    html = INDEX.read_text()
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == [], f"page loads external assets: {external}"
    assert "cdn" not in html.lower()


def test_the_page_needs_no_session_but_the_api_does(client):
    """The shell loads for anyone; it discovers on boot that it must sign in."""
    assert client.get("/").status_code == 200
    assert client.get("/api/me").status_code == 401


# ── The JS ↔ HTML contract ────────────────────────────────────────────────────

def test_every_element_the_client_uses_exists_in_the_page():
    missing = sorted(_ids_used_by_js() - _element_ids_in_html())
    assert missing == [], f"app.js reaches for ids that are not in index.html: {missing}"


def test_the_page_has_no_orphaned_controls():
    """
    A control the client never reads is decoration: it renders, it looks
    functional, and nothing happens (learnings P25's corollary).
    """
    # Ids that exist purely as CSS/layout anchors, not as controls.
    LAYOUT_ONLY = {"gate", "app"}
    orphans = sorted(_element_ids_in_html() - _ids_used_by_js() - LAYOUT_ONLY)
    assert orphans == [], f"index.html has controls nothing reads: {orphans}"


# ── The JS ↔ API contract ─────────────────────────────────────────────────────

def test_every_api_path_the_client_calls_exists(app):
    """A renamed endpoint must fail here, not in someone's browser."""
    declared = set(app.openapi()["paths"])
    normalised = {re.sub(r"\{[^}]+\}", "{param}", p) for p in declared}

    called = _api_paths_called_by_js()
    assert called, "no API calls found in app.js — did the parser stop matching?"

    missing = sorted(p for p in called if p not in normalised)
    assert missing == [], f"app.js calls endpoints the app does not serve: {missing}"


def test_the_client_calls_the_endpoints_that_matter():
    """
    An exact enumeration (learnings P29): if a feature's call disappears from
    the client, this fails rather than a floor quietly still being met.
    """
    assert _api_paths_called_by_js() == {
        "/api/session",
        "/api/me",
        "/api/me/llm-key",
        "/api/filters",
        "/api/searches",
        "/api/searches/{param}",
        "/api/searches/{param}/results",
        "/api/summaries",
        "/api/summaries/{param}",
    }


# ── Safety of what the client renders ─────────────────────────────────────────

def test_the_client_never_builds_markup_from_paper_data():
    """
    Paper titles and author lists come from external APIs. Assigning them to
    innerHTML would make a crafted title executable in a colleague's browser;
    the client uses textContent throughout.
    """
    source = APP_JS.read_text()
    assert "innerHTML" not in source


def test_result_links_do_not_leak_the_referrer():
    source = APP_JS.read_text()
    assert source.count('rel = "noopener noreferrer"') >= 2
