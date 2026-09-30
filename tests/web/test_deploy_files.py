"""
Tests for the deployment files.

Spec: docs/implementation_plan_2026-09-15.md#2.6, W7.a, W7.b

These check what can be checked without a Docker daemon: that the image
installs the right dependency file, that every path it copies exists, and that
.env.example documents every variable the code actually reads. Building and
running the image is a manual step, recorded in the README's deploy checklist.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

ROOT = Path(__file__).parent.parent.parent
DOCKERFILE = ROOT / "Dockerfile"
RAILWAY = ROOT / "railway.json"
ENV_EXAMPLE = ROOT / ".env.example"
GITIGNORE = ROOT / ".gitignore"


def _dockerfile_lines():
    return [l.strip() for l in DOCKERFILE.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


# ── Dockerfile ────────────────────────────────────────────────────────────────

def test_dockerfile_exists():
    assert DOCKERFILE.exists()


def test_dockerfile_installs_web_requirements_not_pyqt():
    """
    The desktop GUI must never be installed in a headless container.

    Checks instruction lines, not the file text: the first version of this
    matched the comment above explaining why PyQt6 is excluded (learnings P19's
    corollary — never match a bare substring against text that also has prose).
    """
    instructions = " ".join(_dockerfile_lines()).lower()
    # The hashed lock generated from requirements-web.txt (review B10).
    assert "--require-hashes -r requirements-web.lock" in instructions
    assert not re.search(r"pip install[^\n]*\brequirements\.txt", instructions)
    assert "pyqt" not in instructions


def test_every_path_the_image_copies_exists():
    """A COPY of a path that does not exist fails the build — catch it here."""
    missing = []
    for line in _dockerfile_lines():
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()[1:]
        for source in parts[:-1]:          # the last token is the destination
            if source.startswith("--"):
                continue
            if not (ROOT / source).exists():
                missing.append(source)
    assert missing == [], f"Dockerfile copies paths that do not exist: {missing}"


def test_the_image_runs_the_app_with_uvicorn():
    text = DOCKERFILE.read_text()
    assert "uvicorn web.app:app" in text
    assert "--host 0.0.0.0" in text


def test_the_app_process_does_not_run_as_root():
    """
    Either a USER instruction, or an entrypoint that drops privileges. The
    image starts as root deliberately — only long enough to make a mounted
    volume writable, which a build-time chown cannot do because the mount
    replaces the directory.
    """
    lines = _dockerfile_lines()
    user_lines = [l for l in lines if l.upper().startswith("USER ")]
    if user_lines:
        assert user_lines[-1].split()[1] != "root"
        return

    entrypoints = [l for l in lines if l.upper().startswith("ENTRYPOINT")]
    assert entrypoints, "neither a USER instruction nor an entrypoint — runs as root"
    script = ROOT / "docker-entrypoint.sh"
    assert script.exists(), "the entrypoint script named in the Dockerfile is missing"
    body = script.read_text()
    assert "gosu" in body, "the entrypoint does not drop privileges"
    # `exec` replaces the process image and cannot call a shell function; an
    # earlier version execed one and exited 127 before the app ever started.
    # This asserted that broken form and blessed it — so the check now names
    # what must be true, and the end-to-end test below proves it.
    assert 'exec gosu "$APP_USER" "$@"' in body


ENTRYPOINT = ROOT / "docker-entrypoint.sh"


def _run_entrypoint_guard(target_user_can_write: bool, tmp_path) -> str:
    """Source the entrypoint and ask it whether it would chown.

    Runs the real decision with a stubbed `gosu`, rather than grepping the
    script for the word "chown". The previous version of this test did grep,
    and stayed green while the guard was inverted — it tested whether ROOT
    could write the directory, which root always can, so the chown fired only
    when it was already unnecessary (learnings P26: the fix commit is the
    least-reviewed code in a change).
    """
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gosu = bin_dir / "gosu"
    gosu.write_text(
        "#!/bin/sh\n"
        "shift\n"                       # drop the username argument
        'if [ "$1" = "test" ] && [ "$2" = "-w" ]; then\n'
        '  [ "${GOSU_TEST_WRITABLE:-0}" = "1" ]\n'
        "  exit $?\n"
        "fi\n"
        'exec "$@"\n'
    )
    gosu.chmod(0o755)

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["GOSU_TEST_WRITABLE"] = "1" if target_user_can_write else "0"
    env["DATA_DIR"] = str(data_dir)

    script = (
        "ENTRYPOINT_SOURCE_ONLY=1 . " + str(ENTRYPOINT) + "\n"
        "if needs_chown; then echo CHOWN; else echo SKIP; fi\n"
    )
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                            env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_entrypoint_chowns_when_the_app_user_cannot_write(tmp_path):
    """
    The case this exists for: the platform mounts a root-owned volume over
    /data, so the unprivileged user cannot write it.
    """
    assert _run_entrypoint_guard(target_user_can_write=False, tmp_path=tmp_path) \
        == "CHOWN"


def test_the_entrypoint_skips_the_chown_when_the_volume_is_already_right(tmp_path):
    """chown -R on every start would be wasted work once PDFs accumulate."""
    assert _run_entrypoint_guard(target_user_can_write=True, tmp_path=tmp_path) \
        == "SKIP"


def test_the_entrypoint_tests_writability_as_the_target_user_not_as_root():
    """
    The specific inversion that made the previous fix useless: asking `-O`/`-w`
    about the *current* user answers a question about root.
    """
    body = ENTRYPOINT.read_text()
    guard = body[body.index("needs_chown() {"):body.index("main() {")]
    assert 'gosu "$APP_USER" test -w' in guard, \
        "the writability test does not run as the target user"
    assert "[ ! -O " not in guard
    assert "[ ! -w " not in guard


def test_the_entrypoint_is_copied_and_made_executable():
    text = DOCKERFILE.read_text()
    assert "COPY docker-entrypoint.sh" in text
    assert "chmod +x /usr/local/bin/docker-entrypoint.sh" in text


def test_a_dockerignore_keeps_secrets_out_of_the_build_context():
    ignore = ROOT / ".dockerignore"
    assert ignore.exists(), "no .dockerignore — a filled-in .env rides the context"
    entries = {l.strip() for l in ignore.read_text().splitlines()}
    assert ".env" in entries
    assert "*.db" in entries


def test_env_example_does_not_break_a_local_run():
    """
    The README tells a developer to copy this file and source it. A live
    DATA_DIR=/data would then crash the local run trying to create a directory
    at the filesystem root.
    """
    for line in ENV_EXAMPLE.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == "DATA_DIR":
            pytest.fail("DATA_DIR is set as a live value; a local run would use "
                        f"{value!r} and fail. Comment it out.")


def test_data_lives_on_a_volume_path_not_in_the_image():
    """A container filesystem does not survive a redeploy."""
    text = DOCKERFILE.read_text()
    assert "DATA_DIR=/data" in text


# ── railway.json ──────────────────────────────────────────────────────────────

def test_railway_config_points_at_the_dockerfile_and_a_healthcheck():
    import json
    config = json.loads(RAILWAY.read_text())
    assert config["build"]["builder"] == "DOCKERFILE"
    assert (ROOT / config["build"]["dockerfilePath"]).exists()
    assert config["deploy"]["healthcheckPath"] == "/healthz"


# ── .env.example ──────────────────────────────────────────────────────────────

# Reads whose name the scan below cannot see, each with where the name comes
# from. A new indirect read fails until it is resolved or listed here.
_KNOWN_INDIRECT_READS = {
    ("src/db.py", "env_key"),              # resolve_data_path: a wrapper, callers scanned
    ("src/accounts.py", "name"),           # _env_int: a wrapper, callers scanned
    ("src/llm_config.py", "var"),          # loops over DEFAULT/LEGACY_PROVIDER_ENV
    ("src/llm_config.py", "self.api_key_env"),   # llm_config.yaml providers.*.api_key_env
    ("src/llm_config.py", "model_env"),          # llm_config.yaml providers.*.model_env
}


def _env_read_arg(node):
    """The name argument of os.environ.get / os.getenv / os.environ[...]."""
    import ast
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) \
            and isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
        return node.slice
    if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute) and (
            node.func.attr == "getenv" or (node.func.attr == "get" and isinstance(
                node.func.value, ast.Attribute) and node.func.value.attr == "environ")):
        return node.args[0]
    return None


def _env_vars_read_by_the_code():
    """Every environment variable src/ and web/ actually read.

    Read with ast, not a regex (plan 2026-09-29 T3.3: the regex saw only a
    literal name, so BIORX_PDF_FONT, read through a constant, went
    undocumented). Resolves module constants (a string or a tuple of them),
    functions that pass their first parameter to a read (resolve_data_path,
    _env_int: their callers' literal names), and the provider variables named
    in llm_config.yaml.
    """
    import ast
    import yaml
    trees = {p: ast.parse(p.read_text())
             for p in list((ROOT / "src").rglob("*.py")) + list((ROOT / "web").rglob("*.py"))}
    consts = {}
    for path, tree in trees.items():
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], ast.Name):
                v = n.value
                items = v.elts if isinstance(v, (ast.Tuple, ast.List)) else [v]
                if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in items):
                    consts[(path, n.targets[0].id)] = [e.value for e in items]
    wrappers = set()
    for tree in trees.values():
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            first = fn.args.args[0].arg if fn.args.args else None
            if any(isinstance(a, ast.Name) and a.id == first
                   for a in map(_env_read_arg, ast.walk(fn)) if a is not None):
                wrappers.add(fn.name)
    names, unresolved = set(), set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            arg = _env_read_arg(node)
            if arg is None and isinstance(node, ast.Call) and node.args \
                    and isinstance(node.func, ast.Name) and node.func.id in wrappers:
                arg = node.args[0]
            if arg is None:
                continue
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
            elif isinstance(arg, ast.Name) and (path, arg.id) in consts:
                names.update(consts[(path, arg.id)])
            else:
                unresolved.add((str(path.relative_to(ROOT)), ast.unparse(arg)))
    assert unresolved <= _KNOWN_INDIRECT_READS, \
        f"env reads whose name is not known: {sorted(unresolved - _KNOWN_INDIRECT_READS)}"
    config = yaml.safe_load((ROOT / "llm_config.yaml").read_text())
    for provider in (config.get("providers") or {}).values():
        names.update(provider[k] for k in ("api_key_env", "model_env") if provider.get(k))
    return names


def test_t33_indirect_env_reads_are_seen():
    """The names only an indirect read reveals are in the scan's result."""
    names = _env_vars_read_by_the_code()
    for name in ("BIORX_PDF_FONT", "BIORX_CONTACT_EMAIL", "ACCESS_CODES_FILE",
                 "LOGIN_MAX_FAILURES", "DEFAULT_LLM_PROVIDER", "DEEPSEEK_API_KEY"):
        assert name in names, name


def test_env_example_documents_every_variable_the_code_reads():
    """
    Read from the call sites, not from a hand-kept list, so a new variable
    that nobody documented fails here (W7.b).
    """
    documented = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", ENV_EXAMPLE.read_text(),
                                re.MULTILINE))
    undocumented = sorted(_env_vars_read_by_the_code() - documented)
    assert undocumented == [], f"env vars read but not in .env.example: {undocumented}"


def test_env_example_holds_no_real_secret():
    """It is committed; it must contain placeholders only."""
    text = ENV_EXAMPLE.read_text()
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        _, _, value = line.partition("=")
        value = value.strip()
        assert not value.startswith("sk-"), f"a real-looking key in .env.example: {line}"


def test_t38_local_claude_config_and_reports_are_gitignored():
    """Plan 2026-09-29 T3.8: .claude/launch.json holds local test secrets."""
    import subprocess
    for path in (".claude/launch.json", ".claude/settings.local.json", ".test-qa-report.md"):
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert r.returncode == 0, f"{path} is not ignored"


def test_dotenv_is_gitignored_so_a_filled_in_copy_is_never_committed():
    ignored = GITIGNORE.read_text().splitlines()
    assert ".env" in ignored


def test_the_required_variables_are_all_present():
    """An exact list of what an operator must set (learnings P29)."""
    text = ENV_EXAMPLE.read_text()
    for name in ("ACCESS_CODE", "SESSION_SECRET", "KEY_ENC_SECRET", "LLM_PROVIDER",
                 "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "DATA_DIR"):
        assert re.search(rf"^#?\s*{name}=", text, re.MULTILINE), f"{name} not documented"


# ── README ────────────────────────────────────────────────────────────────────

README = ROOT / "README.md"


def test_readme_documents_local_run_and_deploy():
    """D4: someone must be able to run it and deploy it from the README alone."""
    text = README.read_text()
    for needle in ("uvicorn web.app:app", "requirements-web.txt", "railway.json",
                   "Deploy checklist", "/healthz"):
        assert needle in text, f"README does not mention {needle!r}"


def test_readme_documents_every_required_variable():
    text = README.read_text()
    for name in ("ACCESS_CODE", "SESSION_SECRET", "KEY_ENC_SECRET", "LLM_PROVIDER"):
        assert name in text, f"README does not mention {name}"


def test_readme_says_the_volume_is_required():
    """The single most costly thing to get wrong: no volume, no persistence."""
    text = README.read_text().lower()
    assert "volume" in text and "/data" in text


def test_the_entrypoint_refuses_to_start_on_an_unwritable_volume(tmp_path):
    """
    The non-root branch, run for real. Some platforms enforce a uid, so the
    entrypoint cannot chown anything; it must say why instead of starting and
    dying at the first database write.
    """
    import os
    import subprocess

    unwritable = tmp_path / "locked"
    unwritable.mkdir()
    unwritable.chmod(0o500)          # readable, not writable
    try:
        env = dict(os.environ)
        env["DATA_DIR"] = str(unwritable)
        result = subprocess.run(
            ["sh", str(ENTRYPOINT), "echo", "started"],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert result.returncode == 1, (
            f"the entrypoint started anyway: {result.stdout!r}"
        )
        assert "started" not in result.stdout
        assert "not writable" in result.stderr
        assert str(unwritable) in result.stderr
        # Plan 2026-09-29 T3.2: name the uid that runs, not APP_USER, which
        # never runs when the platform forces a uid.
        assert f"mount the volume writable by uid {os.getuid()}." in result.stderr
        assert "writable by biorx" not in result.stderr
    finally:
        unwritable.chmod(0o700)


def test_the_entrypoint_starts_the_app_on_a_writable_volume(tmp_path):
    """The other half: it must not refuse a volume that is fine."""
    import os
    import subprocess

    data = tmp_path / "ok"
    data.mkdir()
    env = dict(os.environ)
    env["DATA_DIR"] = str(data)
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "echo", "started"],
        capture_output=True, text=True, env=env, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "started" in result.stdout


def test_the_app_refuses_an_unwritable_data_directory_with_a_clear_message(tmp_path):
    """
    The last line of defence, for platforms where the entrypoint cannot fix
    ownership. A bare sqlite "unable to open database file" names no cause.
    """
    import pytest as _pytest

    from src.db import Database

    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with _pytest.raises(PermissionError) as excinfo:
            Database(str(locked / "biorxiv.db"))
        message = str(excinfo.value)
        assert str(locked) in message
        assert "writable" in message
    finally:
        locked.chmod(0o700)


# ── The root branch, run end to end ───────────────────────────────────────────

def _run_root_branch(tmp_path, *, writable_before_chown, chown_succeeds=True,
                     chown_fixes_it=True):
    """Run the entrypoint's root branch with id, gosu and chown stubbed.

    This is the test that was missing. Three successive fixes to this script
    each shipped a new defect — a guard asking about the wrong user, and an
    `exec` of a shell function — while the suite stayed green, because nothing
    ran the path a deployment actually takes (learnings P26: the fix commit is
    the least-reviewed code in a change).
    """
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    marker = tmp_path / "chowned"

    (bin_dir / "id").write_text("#!/bin/sh\necho 0\n")

    if writable_before_chown:
        gosu_test = "exit 0"
    elif chown_fixes_it:
        gosu_test = f'[ -f "{marker}" ]; exit $?'
    else:
        gosu_test = "exit 1"
    (bin_dir / "gosu").write_text(
        "#!/bin/sh\nshift\n"
        f'if [ "$1" = "test" ]; then {gosu_test}; fi\n'
        'exec "$@"\n'
    )

    if chown_succeeds:
        (bin_dir / "chown").write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
    else:
        (bin_dir / "chown").write_text("#!/bin/sh\nexit 1\n")

    for name in ("id", "gosu", "chown"):
        (bin_dir / name).chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["DATA_DIR"] = str(data_dir)
    return subprocess.run(
        ["sh", str(ENTRYPOINT), "echo", "APP-STARTED"],
        capture_output=True, text=True, env=env, timeout=20,
    )


def test_the_root_branch_starts_the_app_when_the_volume_is_already_right(tmp_path):
    result = _run_root_branch(tmp_path, writable_before_chown=True)
    assert result.returncode == 0, result.stderr
    assert "APP-STARTED" in result.stdout
    assert "taking ownership" not in result.stdout      # no pointless chown


def test_the_root_branch_chowns_a_root_owned_volume_then_starts_the_app(tmp_path):
    """The deploy path: the platform mounts a volume the app user cannot write."""
    result = _run_root_branch(tmp_path, writable_before_chown=False)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "taking ownership" in result.stdout
    assert "APP-STARTED" in result.stdout, (
        "the entrypoint fixed the volume and then failed to start the app"
    )


def test_the_root_branch_refuses_to_start_when_the_chown_fails(tmp_path):
    result = _run_root_branch(tmp_path, writable_before_chown=False,
                              chown_succeeds=False)
    assert result.returncode == 1
    assert "APP-STARTED" not in result.stdout
    assert "could not chown" in result.stderr


def test_the_root_branch_refuses_to_start_if_the_chown_did_not_help(tmp_path):
    result = _run_root_branch(tmp_path, writable_before_chown=False,
                              chown_succeeds=True, chown_fixes_it=False)
    assert result.returncode == 1
    assert "APP-STARTED" not in result.stdout
    assert "still not writable" in result.stderr


def test_downloaded_pdfs_go_to_the_data_volume(monkeypatch, tmp_path):
    """
    Every other write path is environment-driven. PDFHandler was not, so in a
    container PDFs landed in the ephemeral home rather than the mounted volume,
    contradicting what the Dockerfile and README say about persistence.
    """
    from src.pdf_handler import DEFAULT_PDF_DIR, default_pdf_dir

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert default_pdf_dir() == str(tmp_path / "pdfs")

    monkeypatch.delenv("DATA_DIR")
    assert default_pdf_dir() == DEFAULT_PDF_DIR      # the desktop location


def test_s3_image_ships_filter_vocabulary():
    """src/filter_vocabulary.py reads filter_vocabulary.yaml at the repo root;
    without it in the image every search fails (review S3)."""
    text = (ROOT / "Dockerfile").read_text()
    copy_lines = [l for l in text.splitlines() if l.upper().startswith("COPY ")]
    assert any("filter_vocabulary.yaml" in l.split() for l in copy_lines)


# ── S4: one search engine ─────────────────────────────────────────────────────

def test_s4_legacy_agent_removed():
    """The bioRxiv-only search agent and its key_terms.json were a second engine
    with no empty-filter guard (review S4)."""
    assert not (ROOT / "agents" / "search_agent.py").exists()
    assert not (ROOT / "key_terms.json").exists()
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "web").rglob("*.py")) \
            + list((ROOT / "agents").glob("*.py")) + [ROOT / "run.sh"]:
        text = path.read_text()
        assert "search_agent" not in text and "key_terms" not in text, path


def test_s4_run_sh_search_uses_monitor():
    import subprocess
    text = (ROOT / "run.sh").read_text()
    assert 'agents/monitor.py --all' in text
    assert "test_components.py" not in text          # the file does not exist
    assert subprocess.run(["bash", "-n", str(ROOT / "run.sh")]).returncode == 0


def test_m37_docs_state_python_312():
    """README and CLAUDE.md name the version CI and the image run (review D4)."""
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    assert re.findall(r'python-version:\s*"([\d.]+)"', workflow) == ["3.12"]
    assert "FROM python:3.12-" in (ROOT / "Dockerfile").read_text()
    for doc in ("README.md", "CLAUDE.md"):
        text = (ROOT / doc).read_text()
        assert "Python 3.12" in text, doc
        assert not re.search(r"Python 3\.(?!12\b)\d+\+?", text), doc


def test_m32_image_has_no_personal_filters():
    """The owner's filters.json is local state: not copied, not in the build
    context, not in git. The seed file is what the image carries."""
    copied = " ".join(l for l in _dockerfile_lines() if l.startswith("COPY"))
    assert "filters.seed.json" in copied
    assert not re.search(r"(?<![.\w])filters\.json", copied)
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert "filters.json" in ignored
    assert "filters.json" in (ROOT / ".gitignore").read_text().split()


def _unimported_modules(root):
    """Modules under src/ that no file outside tests/ imports."""
    skip = ("tests", "venv", ".git")
    files = [p for p in root.rglob("*.py") if not any(s in p.parts for s in skip)]
    texts = {p: p.read_text() for p in files}
    unused = []
    for p in sorted(root.glob("src/**/*.py")):
        if p.name == "__init__.py":
            continue
        mod = ".".join(p.relative_to(root).with_suffix("").parts)
        parent, name = mod.rsplit(".", 1)
        pats = [rf"\b{re.escape(mod)}\b",
                rf"from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{name}\b",
                rf"from\s+\.{name}\b", rf"from\s+\.\s+import\s+[^\n]*\b{name}\b"]
        if not any(re.search(x, t) for q, t in texts.items() if q != p for x in pats):
            unused.append(mod)
    return unused


def test_m34_no_dead_modules(tmp_path):
    """Review M34: no module in src/ is left that nothing imports."""
    assert _unimported_modules(ROOT) == []
    assert not (ROOT / "src/sources/cache.py").exists()
    from src.db import Database
    assert not hasattr(Database, "bookmark_paper")
    assert not hasattr(Database, "get_bookmarked_papers")
    # The scan can fail: a module only tests import is reported.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "used.py").write_text("")
    (tmp_path / "src" / "orphan.py").write_text("")
    (tmp_path / "app.py").write_text("from src import used\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import src.orphan\n")
    assert _unimported_modules(tmp_path) == ["src.orphan"]
    # run.sh with no command prints usage; it does not launch the GUI.
    run_sh = (ROOT / "run.sh").read_text()
    assert re.search(r'^\s*""\|help\)\s+usage ;;', run_sh, re.MULTILINE)



def test_d1_desktop_app_is_retired():
    """Decision D1 (2026-09-30): gui.py is retired, with its launcher, its
    tests, and what only it used; PyQt6 is no longer a dependency."""
    for gone in ("gui.py", "run_gui.sh", "src/selection.py",
                 "tests/test_gui_filters.py", "tests/test_source_picker.py"):
        assert not (ROOT / gone).exists(), gone
    for req in ("requirements.txt", "requirements-web.txt"):
        assert not re.search(r"^\s*pyqt", (ROOT / req).read_text(), re.I | re.M), req
    assert "gui" not in (ROOT / "run.sh").read_text()
    from src.db import Database
    for method in ("add_to_reference_list", "get_reference_lists",
                   "get_reference_list_items", "remove_from_reference_list"):
        assert not hasattr(Database, method), method
