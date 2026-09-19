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
    # Scripts and stylesheets only: an ordinary <a href> to another site (the
    # DeepSeek key help) is a link the user clicks, not an asset the page loads.
    external = (re.findall(r'<script[^>]*\ssrc="(https?://[^"]+)"', html)
                + re.findall(r'<link[^>]*\shref="(https?://[^"]+)"', html)
                + re.findall(r'<img[^>]*\ssrc="(https?://[^"]+)"', html))
    assert external == [], f"page loads external assets: {external}"
    # Guard-the-guard: the patterns do match an external asset.
    assert re.findall(r'<script[^>]*\ssrc="(https?://[^"]+)"', '<script src="https://cdn.x/a.js">')
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
        "/api/session/recover",
        "/api/session/lookup",
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
        "/api/searches/{param}/summaries",
        "/api/searches/{param}/summaries.pdf",
        "/api/summaries",
        "/api/summaries/lookup",
        "/api/summaries/{param}",
        "/api/references",
        "/api/references/{param}",
        "/api/references/{param}/items",
        "/api/references/{param}/items/{param}",
        "/api/references/{param}/export.csv",
        "/api/references/{param}/summaries",
        "/api/references/{param}/summaries.pdf",
        "/api/references/{param}/pdf/{param}",
        "/api/discover-terms",
        "/api/discover-terms/{param}",
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
    match = re.search(r"function applyDefaultSources\(enabledIds, savedRaw, serverDefaultIds\) \{.*?\n\}",
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

    saved = {"text_groups": [{"both": "maternal, stress"}], "days_back": 30,
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


# ── FE: the filter editor writes what the server reads (2026-09-17 /csdp) ─────

def _node_eval(snippets, expression):
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = "\n".join(snippets) + f"\nconsole.log(JSON.stringify({expression}));"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def _js_block(pattern):
    match = re.search(pattern, APP_JS.read_text(), re.DOTALL)
    assert match, f"not found in app.js: {pattern}"
    return match.group(0)


def test_fe1_text_group_fields_are_the_ones_filtering_reads():
    """FE1: the editor saved groups as {keywords}, a key src/filtering.py and
    the query builder never read, so a saved filter matched everything."""
    import inspect
    from src import filtering
    from src.sources import query_builder
    fields = _node_eval([_js_block(r"const TEXT_GROUP_FIELDS = \[.*?\];")],
                        "TEXT_GROUP_FIELDS.map(f => f[0])")
    assert set(fields) == {"title", "abstract", "both"}
    for module in (filtering, query_builder):
        src = inspect.getsource(module)
        for field in fields:
            assert f'group.get("{field}"' in src, f"{module.__name__} does not read {field}"


def test_fe1_saved_group_filters_papers_adversarial():
    """A group built by the client's normaliser, run through the server's real
    filter: a zebrafish filter must reject a cortisol paper. A legacy
    {keywords} group is migrated into `both` and still filters."""
    from src.filtering import filter_papers
    groups = _node_eval(
        [_js_block(r"function normaliseTextGroup\(g\) \{.*?\n\}")],
        '[normaliseTextGroup({title: "zebrafish"}), normaliseTextGroup({keywords: "zebrafish"})]')
    paper = {"title": "Cortisol and maternal stress", "abstract": "Human cohort."}
    for g in groups:
        assert filter_papers([paper], {"text_groups": [g]}) == [], g
        assert filter_papers([{**paper, "title": "Zebrafish larvae"}], {"text_groups": [g]}), g


def test_fe2_filter_dict_uses_server_key_names():
    """FE2: institution is a string (filtering.py calls .strip() on it) and dates
    are start_date/end_date (what the query builder reads)."""
    code = _js_without_comments()
    build = re.search(r"function buildFilterDict\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    manual = re.search(r"function manualFilter\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert 'institution: $("filter-institution").value.trim()' in build
    for body in (build, manual):
        assert "f.start_date" in body and "f.end_date" in body
        assert "f.date_from" not in body and "f.date_to" not in body


def test_fe2_server_accepts_the_client_filter_shape():
    """The shape buildFilterDict produces runs through the real server code."""
    from src.filtering import filter_papers
    from src.sources.query_builder import build_europepmc_query
    f = {"text_groups": [{"title": "", "abstract": "", "both": "stress"}],
         "authors": [], "institution": "", "start_date": "2020-01-01",
         "end_date": "2020-12-31", "source_selection": {"all": True, "selected": []}}
    assert filter_papers([{"title": "stress", "abstract": ""}], f)
    q = build_europepmc_query(f)
    assert "2020-01-01" in q and "2020-12-31" in q


def test_fe3_bulk_actions_do_not_swallow_failures():
    """FE3: bulk PDF download and bulk remove report what failed."""
    code = _js_without_comments()
    for name in ("downloadRefPdfs", "removeRefSelected"):
        body = re.search(rf"async function {name}\(.*?\n\}}", code, re.DOTALL).group(0)
        assert "/* skip */" not in body and "{ }" not in body.replace("catch (e) {}", "")
        assert "failures.push" in body, f"{name} does not collect failures"


def test_dt2_client_polls_the_discover_endpoint():
    code = _js_without_comments()
    body = re.search(r"async function pollDiscover\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "/api/discover-terms/" in body
    assert "/api/searches/" not in body


# ── Cold sweep (2026-09-17) ───────────────────────────────────────────────────

def _run_handler(name, api_stub, extra=""):
    """Run one of the client's settings handlers in node with a stubbed api()
    and DOM, returning the notices it produced."""
    source = APP_JS.read_text()
    fns = [re.search(rf"(async )?function {n}\(.*?\n\}}", source, re.DOTALL).group(0)
           for n in ("localSettings", "saveLocalSettings", "clearLocalSettings", name)]
    consts = "\n".join(re.findall(r"^const LS_\w+\s*=.*$", source, re.MULTILINE))
    prelude = """
const store = {};
globalThis.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; },
                            removeItem: k => { delete store[k]; } };
const els = {};
const $ = id => (els[id] = els[id] || { value: "", placeholder: "", textContent: "",
                                         classList: { add(){}, remove(){}, toggle(){} } });
const notices = [];
function notice(m, kind = "error") { notices.push([kind, m]); }
function renderMe() {}
const state = { me: { byo_enabled: true, preferred_model: "" } };
""" + api_stub + extra
    script = prelude + consts + "\n" + "\n".join(fns) + \
        f"\n{name}().then(() => console.log(JSON.stringify(notices)));"
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


FAILING_API = """
async function api(method, path) {
  if (method !== "GET") { const e = new Error("503 Service Unavailable"); e.status = 503; throw e; }
  return state.me;
}"""


def test_cs1_remove_key_does_not_claim_success_when_the_server_fails():
    notices = _run_handler("removeKey", FAILING_API)
    assert ["ok", "Key removed."] not in notices
    assert any(k == "error" and "503" in m for k, m in notices), notices


def test_cs1_save_key_does_not_claim_success_when_the_server_fails():
    notices = _run_handler("saveKey", FAILING_API,
                           '\n$("api-key").value = "sk-new-key-123456"; $("key-provider").value = "anthropic";')
    assert ["ok", "Settings saved."] not in notices
    assert any(k == "error" and "server copy was not updated" in m for k, m in notices), notices


def test_cs1_success_is_still_reported():
    ok_api = "async function api() { return state.me; }"
    assert _run_handler("removeKey", ok_api)[-1] == ["ok", "Key removed."]


def test_cs2_filter_test_reports_unreachable_sources():
    code = _js_without_comments()
    body = re.search(r"async function pollFilterTest\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "sources_failed" in body
    assert "failedSourcesText(job)" in body


# ── 2026-09-18: UI fixes, desktop names (docs/implementation_plan_2026-09-18_…) ──

def _css() -> str:
    return CSS.read_text()


def test_ui1_modal_is_a_fixed_overlay():
    """Without this rule the popup rendered ~2,900 px down the page."""
    rule = re.search(r"\.modal-overlay\s*\{([^}]*)\}", _css())
    assert rule, "no .modal-overlay rule"
    assert "position: fixed" in rule.group(1)
    assert "inset: 0" in rule.group(1)
    code = _js_without_comments()
    assert 'e.key === "Escape"' in code and "closeModal()" in code


def test_ui2_summary_renders_in_the_modal():
    """The summary card below the results table is gone; the summary is drawn
    inside the popup, which startSummary opens first."""
    html = INDEX.read_text()
    assert 'id="summary-card"' not in html
    modal = html[html.index('id="paper-modal"'):]
    for el in ("modal-summary-meta", "modal-summary"):
        assert f'id="{el}"' in modal
    code = _js_without_comments()
    body = re.search(r"async function startSummary\(paper, button\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "await openModal(paper" in body
    render = re.search(r"function renderSummary\(job, result\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert '$("modal-summary")' in render


def test_ui3_notice_is_sticky():
    rule = re.search(r"#notice\s*\{([^}]*)\}", _css())
    assert rule and ("position: sticky" in rule.group(1) or "position: fixed" in rule.group(1))


@pytest.mark.parametrize("checked,total,text,disabled", [
    (0, 0, "Save to Saved References", True),
    (0, 40, "Save all 40 results to Saved References", False),
    (3, 40, "Save selected (3) to Saved References", False),
])
def test_ui4_save_button_label(checked, total, text, disabled):
    got = _node_eval([_js_block(r"function saveButtonLabel\(checked, total\) \{.*?\n\}")],
                     f"saveButtonLabel({checked}, {total})")
    assert got == {"text": text, "disabled": disabled}


def test_ui4_nothing_ticked_saves_all():
    code = _js_without_comments()
    body = re.search(r"async function saveResultsAs\(name\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "state.checkedPapers.size ? Array.from(state.checkedPapers) : null" in body


def test_rn1_labels_match_the_desktop():
    """The web tabs use the desktop app's names, read from gui.py itself."""
    gui = (Path(__file__).parent.parent.parent / "gui.py").read_text()
    # The main window's tabs are added from self.<name>_tab attributes.
    desktop_tabs = re.findall(r'tabs\.addTab\(self\.\w+_tab,\s*"([^"]+)"\)', gui)
    assert desktop_tabs, "could not read the desktop tab names"
    html = INDEX.read_text().replace("&amp;", "&")
    web_tabs = re.findall(r'<button id="tab-\w+" class="tab[^"]*">([^<]+)</button>', html)
    assert [t.strip() for t in web_tabs] == [t.strip() for t in desktop_tabs]
    assert "<h2>Saved Filters</h2>" in html
    assert "Saved searches" not in html


@pytest.mark.parametrize("label,expected", [
    ("Inflammation", "Inflammation – 2026-09-18"),
    ("", "Search – 2026-09-18"),
    ("  cortisol, maternal ", "cortisol, maternal – 2026-09-18"),
])
def test_pf1_default_list_name(label, expected):
    got = _node_eval([_js_block(r"function defaultListName\(label, isoDate\) \{.*?\n\}")],
                     f"defaultListName({label!r}, '2026-09-18')")
    assert got == expected


def test_sp6_references_tab_has_the_pdf_export():
    assert 'id="btn-ref-export-summaries"' in INDEX.read_text()
    assert "/api/references/{param}/summaries.pdf" in _api_paths_called_by_js()


# ── 2026-09-18 second report (docs/implementation_plan_2026-09-18_cache_and_summaries.md) ──

def test_c1_page_and_assets_are_not_served_stale(client):
    for path in ("/", "/static/app.js", "/static/styles.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", path


def test_c2_asset_urls_carry_a_content_hash(client, tmp_path):
    import hashlib
    from web.app import versioned_index
    html = client.get("/").text
    for name in ("app.js", "styles.css"):
        digest = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]
        assert f"/static/{name}?v={digest}" in html, name
    # The hash follows the file: change it and the URL changes.
    (tmp_path / "index.html").write_text('<script src="/static/app.js"></script>')
    (tmp_path / "app.js").write_text("one")
    first = versioned_index(tmp_path)
    (tmp_path / "app.js").write_text("two")
    assert versioned_index(tmp_path) != first


def test_a1_select_all_resets_on_search():
    code = _js_without_comments()
    body = re.search(r"async function startSearch\(payload\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert '$("select-all-results").checked = false' in body
    assert '$("select-all-results").indeterminate = false' in body


def test_b1_save_opens_a_dialog_not_a_toggle():
    html = INDEX.read_text()
    assert 'id="save-list-name-wrap"' not in html
    code = _js_without_comments()
    body = re.search(r"async function saveResults\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "window.prompt(" in body
    assert 'addEventListener("click", saveResults)' in code


@pytest.mark.parametrize("name,expected", [
    ("Inflammation – 2026-09-18", "Inflammation – 2026-09-18 (2)"),
    ("Inflammation – 2026-09-18 (2)", "Inflammation – 2026-09-18 (3)"),
    ("List (9)", "List (10)"),
])
def test_b2_duplicate_name_suggests_next(name, expected):
    got = _node_eval([_js_block(r"function nextListName\(name\) \{.*?\n\}")],
                     f"nextListName({name!r})")
    assert got == expected


def test_b2_taken_name_reopens_the_dialog():
    """Run saveResults in node: the first name is taken (409), the second is
    accepted; the dialog must be shown twice, the second time with the reason
    and the suggested name."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    fns = [_js_block(rf"(async )?function {n}\(.*?\n\}}")
           for n in ("nextListName", "defaultListName", "saveResults", "saveResultsAs")]
    script = """
const prompts = [];
const window = { prompt: (msg, val) => { prompts.push([msg, val]); return val; } };
const state = { jobId: "j1", searchLabel: "Inflammation", checkedPapers: new Set() };
const notices = []; function notice(m, k = "error") { notices.push([k, m]); }
let calls = 0;
async function api(method, path, body) {
  calls++;
  if (calls === 1) { const e = new Error(`You already have a list called '${body.name}'.`); e.status = 409; throw e; }
  return { saved: 3, requested: 3, skipped: [] };
}
""" + "\n".join(fns) + """
saveResults().then(() => console.log(JSON.stringify({prompts, notices})));
"""
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip())
    assert len(result["prompts"]) == 2
    assert "already used" in result["prompts"][1][0]
    assert result["prompts"][1][1].endswith(" (2)")
    assert result["notices"][-1][0] == "ok"


def test_k1_summary_error_is_shown_in_the_popup():
    code = _js_without_comments()
    body = re.search(r"async function startSummary\(paper, button\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "— failed: ${reason}" in body


@pytest.mark.parametrize("saved,server,expected", [
    (None, ["a", "b"], ["a", "b"]),          # nothing saved: the server's defaults
    ("", ["b"], ["b"]),
    (None, [], ["a", "b", "c"]),             # no server defaults either: all
    ('["c"]', ["a"], ["c"]),                 # the user's own choice wins
    ('["gone"]', ["b"], ["b"]),              # stale saved choice: server defaults
])
def test_d1_server_defaults_apply_when_nothing_is_saved(saved, server, expected):
    import json
    got = _node_eval([_js_block(r"function applyDefaultSources\(enabledIds, savedRaw, serverDefaultIds\) \{.*?\n\}")],
                     f"applyDefaultSources(['a','b','c'], {json.dumps(saved)}, {json.dumps(server)})")
    assert got == expected


def test_d1_healthz_carries_default_selected(client):
    sources = client.get("/healthz").json()["sources"]
    assert sources and all("default_selected" in s for s in sources)


def test_d2_failed_sources_text_names_source_and_reason():
    got = _node_eval([_js_block(r"function failedSourcesText\(job\) \{.*?\n\}")],
        'failedSourcesText({sources_failed: ["biorxiv_medrxiv"], '
        'source_problems: {"bioRxiv/medRxiv": "did not respond properly"}})')
    assert got == "bioRxiv/medRxiv did not respond properly. Results are incomplete."
    # Older job payloads without reasons still say something true.
    got = _node_eval([_js_block(r"function failedSourcesText\(job\) \{.*?\n\}")],
                     'failedSourcesText({sources_failed: ["pubmed"]})')
    assert got == "Could not reach: pubmed. Results are incomplete."


def test_d2_every_poller_uses_the_shared_text():
    code = _js_without_comments()
    for fn in ("pollSearch", "pollFilterTest", "pollDiscover"):
        body = re.search(rf"async function {fn}\(\) \{{.*?\n\}}", code, re.DOTALL).group(0)
        assert "failedSourcesText(job)" in body, fn


def test_s2_a_new_summary_never_removes_another():
    got = _node_eval([_js_block(r"function summaryKey\(item\) \{.*?\n\}"),
                      _js_block(r"function mergeSummaries\(existing, incoming\) \{.*?\n\}")],
        'mergeSummaries(['
        '{canonical_id:"a", title:"A", created_at:"2026-09-18T10:00"}], ['
        '{canonical_id:"b", title:"B", created_at:"2026-09-18T11:00"}]).map(s => s.title)')
    assert got == ["B", "A"]
    # Same paper again: replaced, not duplicated.
    got = _node_eval([_js_block(r"function summaryKey\(item\) \{.*?\n\}"),
                      _js_block(r"function mergeSummaries\(existing, incoming\) \{.*?\n\}")],
        'mergeSummaries([{canonical_id:"a", title:"old", created_at:"1"}],'
        ' [{canonical_id:"a", title:"new", created_at:"2"}]).map(s => s.title)')
    assert got == ["new"]


def test_s2_panel_is_above_the_results_and_refreshed_after_a_summary():
    html = INDEX.read_text()
    assert html.index('id="summaries-card"') < html.index('id="results-card"')
    code = _js_without_comments()
    body = re.search(r"async function startSummary\(paper, button\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "mergeSummaries(" in body and "refreshSearchSummaries()" in body


# ── PC13: sign-in page — personal access code + PIN
#    (docs/implementation_plan_2026-09-18_invite_codes.md) ──────────────────────

def test_pc13_sign_in_page_is_code_then_pin():
    ids = _element_ids_in_html()
    for el in ("my-code", "remember-code", "code-next", "my-pin", "my-pin-confirm",
               "pin-sign-in", "use-other-code", "welcome", "show-legacy"):
        assert el in ids, el
    # Gone: creating an account by name, and claiming a name (a code does both).
    for el in ("create-account", "claim-name", "claim-pin", "claim-account"):
        assert el not in ids, el
    html = INDEX.read_text()
    assert 'id="my-pin" type="password"' in html
    assert 'id="my-pin-confirm" type="password"' in html
    assert 'id="remember-code" type="checkbox" checked' in html


def test_pc13_a_remembered_code_goes_straight_to_the_pin():
    code = _js_without_comments()
    gate = re.search(r"async function showGate\(message\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "rememberedCode()" in gate and "lookupCode(" in gate
    look = re.search(r"async function lookupCode\(code\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert '"/api/session/lookup"' in look
    assert "Welcome back, ${who.name}." in look
    # A new code (or a reset PIN) asks for the PIN twice.
    assert '$("my-pin-confirm-wrap").classList.toggle("hidden", gatePinSet)' in look
    assert "at least ${gatePinMin} characters" in look      # from /healthz, not a constant


def test_pc13_sign_in_sends_code_and_pin_and_remembers_only_if_ticked():
    code = _js_without_comments()
    body = re.search(r"async function pinSignIn\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert '{ code: gateCode, pin }' in body
    assert 'rememberCode($("remember-code").checked ? gateCode : "")' in body
    assert "The two PINs are not the same." in body
    other = re.search(r"function useOtherCode\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert 'rememberCode("")' in other


def test_pc13_a_cut_off_session_returns_to_the_sign_in_page_with_the_reason():
    code = _js_without_comments()
    api = re.search(r"async function api\(.*?\n\}", code, re.DOTALL).group(0)
    assert "response.status === 401 && state.me" in api and "showGate(error.message)" in api


def test_pc13_old_name_sign_in_only_while_the_shared_code_is_set():
    code = _js_without_comments()
    gate = re.search(r"async function showGate\(message\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert '$("show-legacy").classList.toggle("hidden", !health.access_code_set)' in gate
    sign_in = re.search(r"async function signIn\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "create" not in sign_in
    recover = re.search(r"async function recoverAccount\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "showRecoveryCode(" in recover
    close = re.search(r'\$\("recovery-done"\)\.addEventListener.*?\}\);', code, re.DOTALL).group(0)
    assert 'textContent = ""' in close          # the recovery code is cleared from the page


def test_deepseek_help_button_toggles_the_guide():
    html = INDEX.read_text()
    assert 'id="help-deepseek-toggle"' in html and 'id="help-deepseek"' in html
    guide = html[html.index('id="help-deepseek"'):html.index('</div>', html.index('id="help-deepseek"'))]
    assert "https://platform.deepseek.com/api_keys" in guide
    assert "deepseek-flash" in guide
    # Every link in the guide opens safely in a new tab.
    assert guide.count('target="_blank"') == guide.count('rel="noopener noreferrer"') == 2
    code = _js_without_comments()
    assert '$("help-deepseek-toggle").addEventListener' in code
    assert '$("help-deepseek").classList.toggle("hidden")' in code


# ── csdp review 2026-09-18 ────────────────────────────────────────────────────

def test_a_redraw_does_not_re_offer_summarize_while_one_runs():
    code = _js_without_comments()
    render = re.search(r"function renderResults\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "state.summarizing.has(paperKey(paper))" in render
    start = re.search(r"async function startSummary\(paper, button\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "if (state.summarizing.has(key)) return;" in start
    assert "state.summarizing.delete(key)" in start


def test_leaving_a_signed_in_page_reloads_so_nothing_carries_over():
    code = _js_without_comments()
    gate = re.search(r"async function showGate\(message\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "location.reload()" in gate and "SS_GATE_MESSAGE" in gate
    boot = re.search(r"async function boot\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "sessionStorage.getItem(SS_GATE_MESSAGE)" in boot


def test_downloads_handle_sign_out_and_network_errors():
    code = _js_without_comments()
    api = re.search(r"async function api\(.*?\n\}", code, re.DOTALL).group(0)
    assert "Could not reach the server" in api
    raw = api[api.index("opts.raw"):]
    assert "showGate(detail)" in raw[:600]
    for fn in ("saveSummariesPdf", "exportRefSummariesPdf", "exportRefCsv"):
        body = re.search(rf"async function {fn}\(\) \{{.*?\n\}}", code, re.DOTALL).group(0)
        assert "catch (e)" in body and "resp.status === 401" in body, fn



def test_a_network_blip_while_polling_keeps_the_summary_busy():
    code = _js_without_comments()
    start = re.search(r"async function startSummary\(paper, button\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "if (e.status === 0) {" in start and "netFailures" in start
    assert "if (inFlight) return;" in start               # no pile-up on a slow link


def test_sign_out_errors_are_shown_and_the_gate_message_is_used_once():
    code = _js_without_comments()
    out = re.search(r"async function signOut\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert "catch (e)" in out and "Could not sign out" in out
    boot = re.search(r"async function boot\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert boot.index("sessionStorage.removeItem(SS_GATE_MESSAGE)") < boot.index('api("GET", "/api/me")')


# ── Plan 2026-09-18: live counts and Run button states (FR3, FR4) ─────────────

@pytest.mark.parametrize("job,text", [
    ({"fetched": 150, "matched": 3, "phase": "Searching arXiv…"},
     "Found 150 · Matched 3 — Searching arXiv…"),
    ({"fetched": 150, "matched": 3, "enriched": 2, "enrich_total": 3, "phase": "Enriching 2/3 papers…"},
     "Found 150 · Matched 3 · Enriched 2/3 — Enriching 2/3 papers…"),
    ({"fetched": 0, "matched": 0, "enriched": 0, "enrich_total": 0, "status": "queued"},
     "Found 0 · Matched 0 — queued"),
    ({}, "Found 0 · Matched 0"),
])
def test_fr3_3_progress_text(job, text):
    import json
    got = _node_eval([_js_block(r"function progressText\(job\) \{.*?\n\}")],
                     f"progressText({json.dumps(job)})")
    assert got == text


def test_fr3_4_both_pollers_show_counts():
    code = _js_without_comments()
    search = re.search(r"async function pollSearch\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    test = re.search(r"async function pollFilterTest\(\) \{.*?\n\}", code, re.DOTALL).group(0)
    assert re.search(r'^\s*\$\("phase"\)\.textContent = progressText\(job\);$', search, re.M)
    assert re.search(r'^\s*\$\("filter-test-status"\)\.textContent = progressText\(job\);$',
                     test, re.M)


@pytest.mark.parametrize("run,label", [
    (None, "Run"),
    ({"id": 7, "status": "running"}, "Running…"),
    ({"id": 7, "status": "done"}, "Done"),
    ({"id": 7, "status": "cancelled"}, "Stopped"),
    ({"id": 7, "status": "error"}, "Failed"),
    ({"id": 8, "status": "done"}, "Run"),       # another filter's run
])
def test_fr4_1_filter_run_label(run, label):
    import json
    got = _node_eval([_js_block(r"function filterRunLabel\(filterId, run\) \{.*?\n\}")],
                     f'filterRunLabel("7", {json.dumps(run)})')
    assert got == label


_RUN_HARNESS = r"""
const created = [];
function mk(tag) {
  const el = { tag, dataset: {}, textContent: "", value: "", disabled: false, checked: false,
    indeterminate: false, className: "", children: [],
    classList: { add() {}, remove() {}, toggle() {} },
    append(...c) { this.children.push(...c); }, appendChild(c) { this.children.push(c); },
    addEventListener(ev, fn) { this.onclick = fn; } };
  created.push(el);
  return el;
}
const document = { createElement: mk };
const els = {};
els["search-filter-list"] = Object.assign(mk("ul"), {
  querySelectorAll: () => els["search-filter-list"].children
    .flatMap(li => li.children || []).filter(c => c.tag === "button" && c.dataset.filterId !== undefined),
});
Object.defineProperty(els["search-filter-list"], "textContent", {
  set(v) { this.children = []; }, get() { return ""; } });
const $ = (id) => els[id] || (els[id] = mk("x"));
let mode = "ok", seenAtPost = null, resultsFail = false;
// Each poll of the job takes the next entry: a job object, or an error to throw.
const polls = [];
function httpError(status) { const e = new Error(`HTTP ${status}`); e.status = status; return e; }
let postBody = null;
async function api(method, path, body) {
  if (method === "POST") postBody = body;
  if (method === "GET" && path === "/api/filters") return { filters: [{ id: 7, name: "A" }, { id: 8, name: "B" }] };
  if (method === "POST") {
    seenAtPost = $("search-filter-list").querySelectorAll().map(b => [b.textContent, b.disabled]);
    if (mode === "refuse") throw new Error("no search terms");
    return { job_id: "j" };
  }
  if (method === "GET" && path === "/api/searches/j") {
    const next = polls.shift();
    if (next instanceof Error) throw next;
    return next;
  }
  if (method === "GET" && path.startsWith("/api/searches/j/results")) {
    if (resultsFail) throw httpError(500);
    return { total: 0, results: [], status: "done" };
  }
  return {};
}
const job = (status) => ({ status, fetched: 3, matched: 0, phase: "", sources_failed: [] });
function notice() {} function renderSummariesPanel() {} function renderResults() {}
function refreshSearchSummaries() {} function failedSourcesText() { return ""; }
function populateCategorySelect() {} function getSourceSelection() { return { all: true }; }
const POLL_MS = 1000;
const POLL_GIVE_UP = 8;
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
const labels = () => $("search-filter-list").querySelectorAll().map(b => [b.textContent, b.disabled]);
"""


def _run_flow(body):
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    parts = [
        _js_block(r"const state = \{.*?\n\};"),
        _RUN_HARNESS,
        _js_block(r"async function loadSearchFilters\(\) \{.*?\n\}"),
        _js_block(r"function filterRunLabel\(filterId, run\) \{.*?\n\}"),
        _js_block(r"function renderFilterRunButtons\(\) \{.*?\n\}"),
        _js_block(r"async function startSearch\(payload\) \{.*?\n\}"),
        _js_block(r"function searchFinished\(status\) \{.*?\n\}"),
        _js_block(r"function stopPolling\(\) \{.*?\n\}"),
        _js_block(r"async function pollSearch\(\) \{.*?\n\}"),
        _js_block(r"async function loadResults\(\) \{.*?\n\}"),
        _js_block(r"function progressText\(job\) \{.*?\n\}"),
        _js_block(r"function enrichProblemsText\(job\) \{.*?\n\}"),
        _js_block(r"function shouldStopPolling\(error, failuresInARow\) \{.*?\n\}"),
        "(async () => { const out = {};\n" + body + "\nconsole.log(JSON.stringify(out)); })();",
    ]
    result = subprocess.run([node, "-e", "\n".join(parts)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_fr4_2_buttons_disabled_before_request():
    """FR4.2: clicking Run disables every Run button and labels the clicked one
    Running… before the POST leaves; the job finishing (through the real
    pollSearch) labels it Done and re-enables. Clicking Done runs it again."""
    out = _run_flow("""
      await loadSearchFilters();
      out.initial = labels();
      const buttons = $("search-filter-list").querySelectorAll();
      polls.push(job("done"));
      await buttons[0].onclick();
      out.atPost = seenAtPost;
      await new Promise(r => setTimeout(r, 0));
      out.done = labels();
      polls.push(job("cancelled"));
      await buttons[0].onclick();
      out.againAtPost = seenAtPost;
      await new Promise(r => setTimeout(r, 0));
      out.stopped = labels();
    """)
    assert out["initial"] == [["Run", False], ["Run", False]]
    assert out["atPost"] == [["Running…", True], ["Run", True]]
    assert out["done"] == [["Done", False], ["Run", False]]
    assert out["againAtPost"] == [["Running…", True], ["Run", True]]
    assert out["stopped"] == [["Stopped", False], ["Run", False]]


def test_fr4_2_refused_run_returns_to_run():
    """An empty filter refused by the server never ran: back to Run, not Failed."""
    out = _run_flow("""
      await loadSearchFilters();
      mode = "refuse";
      await $("search-filter-list").querySelectorAll()[0].onclick();
      out.after = labels();
    """)
    assert out["after"] == [["Run", False], ["Run", False]]


def test_fr4_3_rerender_keeps_state():
    """FR4.3: the list is rebuilt after a filter is saved; the label survives."""
    out = _run_flow("""
      await loadSearchFilters();
      polls.push(job("running"));
      await $("search-filter-list").querySelectorAll()[1].onclick();
      out.midRun = (await loadSearchFilters(), labels());
      polls.push(job("done"));
      await pollSearch();
      await loadSearchFilters();
      out.after = labels();
    """)
    assert out["midRun"] == [["Run", True], ["Running…", True]]
    assert out["after"] == [["Run", False], ["Done", False]]


def test_fr4_4_one_failed_check_does_not_end_the_search():
    """Review finding 1 (P1): a failed status check is a blip, not a failed
    search. The button stays Running… and disabled, and a later check that
    finds the job done still ends in Done."""
    out = _run_flow("""
      await loadSearchFilters();
      polls.push(httpError(0));
      await $("search-filter-list").querySelectorAll()[0].onclick();
      await new Promise(r => setTimeout(r, 0));
      out.afterBlip = labels();
      polls.push(httpError(502), job("done"));
      await pollSearch(); out.afterSecond = labels();
      await pollSearch(); out.after = labels();
    """)
    assert out["afterBlip"] == [["Running…", True], ["Run", True]]
    assert out["afterSecond"] == [["Running…", True], ["Run", True]]
    assert out["after"] == [["Done", False], ["Run", False]]


@pytest.mark.parametrize("errors", [
    ["httpError(0)"] * 8,     # gave up after repeated failures
    ["httpError(410)"],       # expired: a definite answer, no retrying
])
def test_fr4_4_giving_up_says_lost_track_not_failed(errors):
    """When the page stops tracking, the search may well have finished on the
    server — so the label is Lost track, never Failed or Done."""
    out = _run_flow(f"""
      await loadSearchFilters();
      polls.push({", ".join(errors)});
      await $("search-filter-list").querySelectorAll()[0].onclick();
      await new Promise(r => setTimeout(r, 0));
      for (let i = 1; i < {len(errors)}; i++) await pollSearch();
      out.after = labels();
    """)
    assert out["after"] == [["Lost track", False], ["Run", False]]


def test_fr4_4_failed_results_load_still_releases_buttons():
    """The job finished but its results could not be loaded: the buttons must
    not stay disabled."""
    out = _run_flow("""
      await loadSearchFilters();
      resultsFail = true;
      polls.push(job("done"));
      await $("search-filter-list").querySelectorAll()[0].onclick();
      await new Promise(r => setTimeout(r, 0));
      out.after = labels();
    """)
    assert out["after"] == [["Done", False], ["Run", False]]


def test_i1_saved_filter_runs_on_its_own_sources():
    """Issue 1: running a saved filter must not send the Search panel's source
    boxes (the server then uses the filter's saved sources); the manual search
    still sends them."""
    out = _run_flow("""
      await loadSearchFilters();
      polls.push(job("done"));
      await $("search-filter-list").querySelectorAll()[0].onclick();
      out.saved = postBody;
      await new Promise(r => setTimeout(r, 0));
      polls.push(job("done"));
      await startSearch({ filter: { text_groups: [{ both: "x" }] } });
      out.manual = postBody;
    """)
    assert "source_selection" not in out["saved"] and out["saved"]["filter_id"] == 7
    assert out["manual"]["source_selection"] == {"all": True}


@pytest.mark.parametrize("problems,text", [
    ({}, ""),
    ({"Crossref": [12, 124]},
     "Crossref could not be reached for 12 of 124 papers — some PDF links or details may be missing."),
    ({"Crossref": [1, 2], "Unpaywall": [2, 2]},
     "Crossref could not be reached for 1 of 2 papers; Unpaywall could not be reached for 2 of 2 "
     "papers — some PDF links or details may be missing."),
])
def test_i3_enrich_problems_text(problems, text):
    import json
    got = _node_eval([_js_block(r"function enrichProblemsText\(job\) \{.*?\n\}")],
                     f"enrichProblemsText({json.dumps({'enrich_problems': problems})})")
    assert got == text


def test_i3_search_page_shows_enrichment_outage():
    """Issue 3: the real pollSearch puts the note on the page."""
    out = _run_flow("""
      await loadSearchFilters();
      polls.push(Object.assign(job("done"), { enrich_problems: { Crossref: [3, 5] } }));
      await $("search-filter-list").querySelectorAll()[0].onclick();
      await new Promise(r => setTimeout(r, 0));
      out.note = $("enrich-problems").textContent;
    """)
    assert out["note"].startswith("Crossref could not be reached for 3 of 5 papers")



@pytest.mark.parametrize("status,failures,stop", [
    (0, 1, False), (502, 7, False), (0, 8, True),
    (401, 1, True), (404, 1, True), (410, 1, True),
])
def test_i5_should_stop_polling(status, failures, stop):
    got = _node_eval(["const POLL_GIVE_UP = 8;",
                      _js_block(r"function shouldStopPolling\(error, failuresInARow\) \{.*?\n\}")],
                     f"shouldStopPolling({{status: {status}}}, {failures})")
    assert got is stop


def _run_filter_test(body):
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    harness = r"""
const els = {};
const $ = (id) => els[id] || (els[id] = { textContent: "", classList: { add() {}, remove() {} } });
const POLL_GIVE_UP = 8;
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
const polls = [];
function httpError(status) { const e = new Error(`HTTP ${status}`); e.status = status; return e; }
async function api(method, path) {
  if (path.includes("/results")) return { total: 2, results: [] };
  const next = polls.shift();
  if (next instanceof Error) throw next;
  return next;
}
function failedSourcesText() { return ""; } function renderFilterTestResults() {}
"""
    parts = [
        _js_block(r"const state = \{.*?\n\};"),
        harness,
        _js_block(r"function shouldStopPolling\(error, failuresInARow\) \{.*?\n\}"),
        _js_block(r"function progressText\(job\) \{.*?\n\}"),
        _js_block(r"function enrichProblemsText\(job\) \{.*?\n\}"),
        _js_block(r"async function pollFilterTest\(\) \{.*?\n\}"),
        "(async () => { const out = {}; state.filterTestJobId = 't'; state.filterTestPolling = 1;\n"
        + body + "\nconsole.log(JSON.stringify(out)); })();",
    ]
    result = subprocess.run([node, "-e", "\n".join(parts)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_i5_filter_test_survives_one_failed_check():
    """Issue 5: the Filters-tab test kept no count — one failed check ended it."""
    out = _run_filter_test("""
      polls.push(httpError(0), { status: "done", fetched: 5, matched: 2, sources_failed: [] });
      await pollFilterTest(); out.afterBlip = $("filter-test-status").textContent;
      await pollFilterTest(); out.after = $("filter-test-status").textContent;
    """)
    assert out["afterBlip"].startswith("Lost contact with the server — retrying (1/8)")
    assert out["after"] == "2 papers matched."


def test_i5_filter_test_gives_up_on_expired():
    out = _run_filter_test("""
      polls.push(httpError(410));
      await pollFilterTest(); out.after = $("filter-test-status").textContent;
      out.polling = state.filterTestPolling;
    """)
    assert out["after"] == "Lost track of the test: HTTP 410" and out["polling"] is None


# ── RL: saved reference lists — details, summaries, save (2026-09-18) ─────────

_REF_HARNESS = r"""
function mk(tag) {
  const el = { tag, dataset: {}, textContent: "", className: "", checked: false, disabled: false,
    style: {}, children: [], href: "", click() {},
    classList: { add() {}, remove() {}, toggle() {} },
    append(...c) { this.children.push(...c); }, appendChild(c) { this.children.push(c); },
    addEventListener(ev, fn) { this["on" + ev] = fn; },
    querySelectorAll(sel) {
      const all = []; const walk = (n) => { for (const c of n.children || []) { all.push(c); walk(c); } };
      walk(this);
      if (sel === "input:checked") return all.filter(c => c.tag === "input" && c.checked);
      return all;
    } };
  return el;
}
const document = { createElement: mk };
const els = {};
const $ = (id) => els[id] || (els[id] = mk("x"));
Object.defineProperty($("ref-papers-body"), "textContent", { set() { this.children = []; }, get() { return ""; } });
const opened = [], summarized = [], calls = [];
function openModal(p) { opened.push(p.title); }
function startSummary(p, btn) { summarized.push([p.title, btn.dataset.paperKey]); }
function notice() {} function updateRefCheckedCount() {}
function safeUrl(u) { return u || ""; }
let summariesReply = { summaries: [] };
async function api(method, path, body, opts) {
  calls.push(path);
  if (path.endsWith("/summaries")) return summariesReply;
  if (path.endsWith("/items")) return { items: state.refItems };
  return { status: 200, ok: true, blob: async () => ({}) };
}
const URL = { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} };
globalThis.setTimeout = (fn) => 0;
function rows() {
  return $("ref-papers-body").children.map(tr => {
    const title = tr.children[1];
    const tags = title.children.slice(1).map(t => t.textContent);
    const btn = tr.children[5].children[0];
    return { tags, button: btn.textContent, key: btn.dataset.paperKey };
  });
}
"""


def _run_ref(body):
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    parts = [
        _js_block(r"const state = \{.*?\n\};"),
        _REF_HARNESS,
        _js_block(r"function paperKey\(paper\) \{.*?\n\}"),
        _js_block(r"function summaryKey\(item\) \{.*?\n\}"),
        _js_block(r"function hasSummary\(paper, summaries = state\.searchSummaries\) \{.*?\n\}"),
        _js_block(r"async function refreshRefSummaries\(listId\) \{.*?\n\}"),
        _js_block(r"function renderRefItems\(\) \{.*?\n\}"),
        _js_block(r"async function exportRefSummariesPdf\(\) \{.*?\n\}"),
        """state.refItems = [
          { item_id: 11, paper: { title: "Summarized one", canonical_id: "doi:1", doi: "1" } },
          { item_id: 12, paper: { title: "Not yet", canonical_id: "doi:2", doi: "2" } }];
        state.activeListId = 5; state.refLists = [{ id: 5, name: "L" }];""",
        "(async () => { const out = {};\n" + body + "\nconsole.log(JSON.stringify(out)); })();",
    ]
    result = subprocess.run([node, "-e", "\n".join(parts)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_rl2_list_rows_offer_details_badge_and_summarize():
    out = _run_ref("""
      summariesReply = { summaries: [{ canonical_id: "doi:1", title: "Summarized one" }] };
      await refreshRefSummaries(5);
      out.rows = rows();
      const first = $("ref-papers-body").children[0];
      first.children[1].children[1].onclick();          // detail
      first.children[5].children[0].onclick();          // Summarize
      $("ref-papers-body").children[1].children[5].children[0].onclick();
      out.opened = opened; out.summarized = summarized;
    """)
    assert out["rows"] == [
        {"tags": ["detail", "✓ Summary"], "button": "Summarize", "key": "doi:1"},
        {"tags": ["detail"], "button": "Summarize", "key": "doi:2"},
    ]
    assert out["opened"] == ["Summarized one"]
    assert out["summarized"] == [["Summarized one", "doi:1"], ["Not yet", "doi:2"]]


def test_rl1_stale_list_reply_is_ignored():
    """A summaries reply for a list the user has already left must not paint badges."""
    out = _run_ref("""
      summariesReply = { summaries: [{ canonical_id: "doi:2" }] };
      state.activeListId = 6;
      await refreshRefSummaries(5);
      out.summaries = state.refSummaries;
    """)
    assert out["summaries"] == []


def test_rl3_save_summaries_sends_the_ticked_items():
    out = _run_ref("""
      renderRefItems();
      $("ref-papers-body").children[1].children[0].children[0].checked = true;
      await exportRefSummariesPdf();
      out.ticked = calls.filter(c => c.includes("summaries.pdf"));
      $("ref-papers-body").children[1].children[0].children[0].checked = false;
      await exportRefSummariesPdf();
      out.all = calls.filter(c => c.includes("summaries.pdf"));
    """)
    assert out["ticked"] == ["/api/references/5/summaries.pdf?item_ids=12"]
    assert out["all"][-1] == "/api/references/5/summaries.pdf"
