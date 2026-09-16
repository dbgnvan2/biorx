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
    assert "requirements-web.txt" in instructions
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
    assert 'exec gosu "$APP_USER"' in body


def _entrypoint_commands() -> str:
    """The entrypoint script with comments and echoed messages stripped.

    The first version of the check below matched the word "chown" inside the
    script's own warning message, so deleting the actual command left it green
    (learnings P19's corollary).
    """
    import re as _re
    body = (ROOT / "docker-entrypoint.sh").read_text()
    body = _re.sub(r"^\s*#.*$", "", body, flags=_re.MULTILINE)
    body = _re.sub(r"^\s*echo .*$", "", body, flags=_re.MULTILINE)
    return body


def test_the_entrypoint_makes_the_mounted_volume_writable():
    """
    The finding this exists for: a build-time `chown /data` does not survive a
    runtime volume mount, so the first write fails with PermissionError.
    """
    import re as _re
    commands = _entrypoint_commands()
    assert _re.search(r'chown\s+-R\s+"\$APP_USER"\s+"\$DATA_DIR"', commands), \
        "the entrypoint does not actually chown the data directory"
    assert 'mkdir -p "$DATA_DIR"' in commands


def test_the_comment_and_echo_stripper_works():
    """Guard-the-guard: if stripping over-reaches, the check above goes blind."""
    commands = _entrypoint_commands()
    assert "#" not in commands.replace("$#", "")
    assert "WARNING" not in commands          # echoed text is gone
    assert "gosu" in commands                 # real commands survive


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

def _env_vars_read_by_the_code():
    """Every environment variable src/ and web/ actually read."""
    names = set()
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "web").rglob("*.py")):
        source = path.read_text()
        names |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', source))
        names |= set(re.findall(r'os\.environ\[\s*"([A-Z0-9_]+)"\s*\]', source))
        names |= set(re.findall(r'os\.getenv\(\s*"([A-Z0-9_]+)"', source))
    return names


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
