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

def _js_without_comments() -> str:
    """app.js with comments stripped.

    Three times now a check like the ones below has matched the comment that
    explains it rather than any code (learnings P19's corollary). Strip the
    prose before looking for the needle.
    """
    source = APP_JS.read_text()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)
    return source


def test_the_comment_stripper_works():
    """The guard-the-guard: if stripping stops working, the checks below go
    blind in the direction of passing."""
    assert "/*" not in _js_without_comments()
    assert "no build step" not in _js_without_comments()   # from the file header
    assert "textContent" in _js_without_comments()         # real code survives


def test_the_client_never_builds_markup_from_paper_data():
    """
    Paper titles and author lists come from external APIs. Assigning them into
    the DOM as markup would make a crafted title executable in a colleague's
    browser; the client sets text, not markup, throughout.
    """
    code = _js_without_comments()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in code, f"app.js assigns paper data via {sink}"


def test_every_url_from_paper_data_passes_through_the_scheme_check():
    """
    A URL from an external API is as untrusted as a title: `javascript:` in an
    href runs on click — the same class of problem through a different door.

    Asserts the assignment form, because a check for "safeUrl appears in the
    same statement as href" has a hole — the value can be built one line
    earlier. The function's own behaviour is tested below.
    """
    code = _js_without_comments()
    paper_urls = re.findall(r"(?:const|let|var)\s+\w*[Hh]ref\s*=\s*([^;]+);", code)
    assert paper_urls, "no URL assignments found — did the client change shape?"
    for expression in paper_urls:
        assert "safeUrl(" in expression, \
            f"a link URL is built without the scheme check: {expression.strip()}"


# The property under test is the SCHEME, not whether the URL is well-formed.
# Junk resolves against the page origin into an ordinary same-origin http(s)
# link — a dead link, not a vector — so it is allowed. Rejecting it would be a
# tidiness rule, and asserting it here would pin a belief the code never held.
@pytest.mark.parametrize("url,expected_safe", [
    ("https://arxiv.org/abs/2609.1", True),
    ("http://example.org/paper", True),
    ("/relative/path", True),
    ("not a url at all", True),          # resolves same-origin; harmless
    ("javascript:alert(document.cookie)", False),
    ("JavaScript:alert(1)", False),
    ("  javascript:alert(1)", False),
    ("java\tscript:alert(1)", False),
    ("data:text/html;base64,PHNjcmlwdD4=", False),
    ("vbscript:msgbox(1)", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_safeurl_behaviour(url, expected_safe):
    """
    Runs the client's own safeUrl in node against hostile input, rather than
    asserting that a line of source exists. Skipped where node is absent.
    """
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; safeUrl behaviour not exercised here")

    source = APP_JS.read_text()
    match = re.search(r"function safeUrl\(value\) \{.*?\n\}", source, re.DOTALL)
    assert match, "safeUrl is no longer defined in app.js"

    script = (
        "globalThis.window = { location: { origin: 'https://app.example' } };\n"
        + match.group(0)
        + f"\nconsole.log(JSON.stringify(safeUrl({json.dumps(url)})));"
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True,
                            timeout=20)
    assert result.returncode == 0, result.stderr
    returned = json.loads(result.stdout.strip())

    if expected_safe:
        assert returned, f"a legitimate URL was rejected: {url!r}"
        assert returned.startswith(("http://", "https://"))
    else:
        assert returned == "", f"an unsafe URL was accepted: {url!r} -> {returned!r}"


def test_every_external_link_sets_noopener_noreferrer():
    """
    An exact pairing, not a floor (learnings P29): every anchor that opens in a
    new tab must also drop the opener and the referrer.
    """
    code = _js_without_comments()
    new_tabs = len(re.findall(r'\.target = "_blank"', code))
    protected = len(re.findall(r'\.rel = "noopener noreferrer"', code))
    assert new_tabs > 0
    assert protected == new_tabs, (
        f"{new_tabs} links open a new tab but only {protected} set rel"
    )
