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
        "/healthz",
        "/api/session",
        "/api/me",
        "/api/me/llm-key",
        "/api/me/llm-model",
        "/api/filters",
        "/api/filters/{param}",
        "/api/filters/{param}/test",
        "/api/searches",
        "/api/searches/{param}",
        "/api/searches/{param}/results",
        "/api/searches/{param}/save-as-list",
        "/api/summaries",
        "/api/summaries/lookup",
        "/api/summaries/{param}",
        "/api/references",
        "/api/references/{param}",
        "/api/references/{param}/items",
        "/api/references/{param}/items/{param}",
        "/api/references/{param}/export.csv",
        "/api/references/{param}/pdf/{param}",
        "/api/discover-terms",
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


# ── Per-user settings (docs/implementation_plan_2026-09-17_per_user_settings.md) ──

def test_ps4_client_never_touches_server_config():
    """The web client has no path to the server's config files."""
    assert not any(p.startswith("/api/settings") for p in _api_paths_called_by_js())
    code = _js_without_comments()
    assert "/api/settings" not in code
    assert "yaml" not in code.lower()
    ids = _element_ids_in_html()
    for gone in ("settings-file-select", "settings-editor",
                 "btn-settings-save", "btn-settings-reload", "toggle-settings"):
        assert gone not in ids, f"server-config editor element still present: {gone}"


def test_ps5_settings_tab_contains_llm_and_sources():
    """The LLM panel and the default-sources picker live inside the Settings tab."""
    from html.parser import HTMLParser

    VOID = {"input", "br", "img", "meta", "link", "hr", "source", "wbr"}

    class Finder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.inside = [], {}

        def handle_starttag(self, tag, attrs):
            el_id = dict(attrs).get("id")
            if el_id:
                self.inside[el_id] = "panel-settings" in self.stack
            if tag not in VOID:
                self.stack.append(el_id)

        def handle_endtag(self, tag):
            if tag not in VOID and self.stack:
                self.stack.pop()

    f = Finder()
    f.feed(INDEX.read_text())
    assert "panel-settings" in f.inside, "no Settings panel in the page"
    for el in ("key-provider", "api-key", "preferred-model", "save-key",
               "default-sources", "btn-save-default-sources"):
        assert f.inside.get(el) is True, f"#{el} is not inside #panel-settings"
    # Guard-the-guard: an element outside the panel must read as outside.
    assert f.inside.get("search-sources") is False


@pytest.mark.parametrize("enabled,saved,expected", [
    (["a", "b", "c"], '["a","c"]', ["a", "c"]),          # normal
    (["a", "b", "c"], '["c","a"]', ["a", "c"]),          # server order kept
    (["a", "b"],      '["a","gone"]', ["a"]),            # stale id dropped
    (["a", "b"],      '["gone"]', ["a", "b"]),           # all stale -> all
    (["a", "b"],      None, ["a", "b"]),                 # nothing saved
    (["a", "b"],      "", ["a", "b"]),                   # empty string
    (["a", "b"],      "{not json", ["a", "b"]),          # corrupt
    (["a", "b"],      '{"a": true}', ["a", "b"]),        # wrong shape
    (["a", "b"],      "[]", ["a", "b"]),                 # empty list
])
def test_ps6_default_sources_logic(enabled, saved, expected):
    """Runs the client's own applyDefaultSources in node. Skipped without node."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; applyDefaultSources not exercised here")

    source = APP_JS.read_text()
    match = re.search(r"function applyDefaultSources\(enabledIds, savedRaw\) \{.*?\n\}",
                      source, re.DOTALL)
    assert match, "applyDefaultSources is no longer defined in app.js"

    script = (match.group(0)
              + f"\nconsole.log(JSON.stringify(applyDefaultSources("
                f"{json.dumps(enabled)}, {json.dumps(saved)})));")
    result = subprocess.run([node, "-e", script], capture_output=True, text=True,
                            timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == expected


def test_ps6_both_pickers_use_the_defaults():
    """The search picker and the new-filter picker are rendered from the saved
    defaults; the default-sources picker itself is too."""
    code = _js_without_comments()
    for picker in ("search-sources", "filter-sources-picker", "default-sources"):
        assert re.search(
            rf'renderSourcePicker\(\$\("{picker}"\), state\.sources, defaultSourceIds\(\)\)',
            code), f"#{picker} is not rendered from the user's default sources"


def test_ps7_storage_access_is_guarded():
    """Every localStorage call for default sources sits inside a try block, so
    a browser that blocks storage still renders the pickers."""
    code = _js_without_comments()
    calls = [m.start() for m in re.finditer(r"localStorage\.\w+\(LS_DEFAULT_SOURCES", code)]
    assert len(calls) == 2, f"expected one read and one write, found {len(calls)}"
    for pos in calls:
        line_start = code.rfind("\n", 0, pos)
        preceding = code[max(0, line_start - 80):pos]
        assert "try" in preceding, "a default-sources storage call is not in a try block"


def test_ps10_client_reads_filters_in_the_shape_the_api_returns(signed_in):
    """Producer/consumer contract (learnings P19): a filter saved through the
    API, read back through GET /api/filters, and passed through the client's
    own filterFields() must yield the saved fields. The parity commit read
    `f.filter`, which the API never returns, so every saved filter opened
    blank and a Save wiped it."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; filterFields not exercised here")

    saved = {"text_groups": [{"keywords": "maternal, stress"}], "days_back": 30,
             "category": "neuroscience",
             "source_selection": {"all": False, "selected": ["pubmed"]}}
    r = signed_in.post("/api/filters", json={"name": "contract", "enabled": True,
                                             "filter": saved})
    assert r.status_code == 201
    listed = [f for f in signed_in.get("/api/filters").json()["filters"]
              if f["name"] == "contract"]
    assert len(listed) == 1

    match = re.search(r"function filterFields\(f\) \{.*?\n\}",
                      APP_JS.read_text(), re.DOTALL)
    assert match, "filterFields is no longer defined in app.js"
    script = (match.group(0)
              + f"\nconsole.log(JSON.stringify(filterFields({json.dumps(listed[0])})));")
    result = subprocess.run([node, "-e", script], capture_output=True, text=True,
                            timeout=20)
    assert result.returncode == 0, result.stderr
    fields = json.loads(result.stdout.strip())
    for key, value in saved.items():
        assert fields.get(key) == value, f"client lost {key!r} reading the API's filter"


def test_ps10_select_filter_reads_through_filter_fields():
    code = _js_without_comments()
    body = re.search(r"function selectFilter\(filterId\) \{.*?\n\}", code, re.DOTALL)
    assert body, "selectFilter is no longer defined"
    assert "filterFields(f)" in body.group(0)
    assert "f.filter" not in body.group(0)
