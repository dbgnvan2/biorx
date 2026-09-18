"""
The web app never reads or writes the server's config files.

Spec:  docs/implementation_plan_2026-09-17_per_user_settings.md#PS1-PS3

A web user's settings are their own and live in their browser. The server's
sources_config.yaml and llm_config.yaml are owner-only: changing them would
change the app for every user (enabled sources, provider URLs, the daily cap on
the owner's key), and reading them would expose the owner's contact email.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

REPO = Path(__file__).parent.parent.parent
CONFIG_FILES = ("sources_config.yaml", "llm_config.yaml")


@pytest.mark.parametrize("filename", CONFIG_FILES)
def test_ps1_settings_routes_removed(signed_in, filename):
    assert signed_in.get(f"/api/settings/{filename}").status_code == 404
    r = signed_in.put(f"/api/settings/{filename}", json={"content": "x: 1\n"})
    assert r.status_code in (404, 405)


def test_ps2_put_does_not_touch_config_files(signed_in):
    """Dirty-state check on the artifacts themselves (learnings P6/P8)."""
    before = {f: (REPO / f).read_bytes() for f in CONFIG_FILES if (REPO / f).exists()}
    assert before, "no config files found to guard — did they move?"
    try:
        for f in CONFIG_FILES:
            signed_in.put(f"/api/settings/{f}", json={"content": "default_provider: evil\n"})
        after = {f: (REPO / f).read_bytes() for f in before}
    finally:
        # If a regression reinstates the write, the real files must not stay
        # clobbered for the rest of the suite (or the developer's next run).
        for f, data in before.items():
            if (REPO / f).read_bytes() != data:
                (REPO / f).write_bytes(data)
    assert after == before


def test_ps3_no_route_returns_config_text(app, signed_in):
    """No route path names a config file or a settings editor, and /healthz
    carries no contact email."""
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if p.startswith("/api/settings")], paths
    assert not [p for p in paths if "yaml" in p.lower()], paths

    body = signed_in.get("/healthz").json()
    assert "contact_email" not in body
    assert "@" not in str(body.get("sources", ""))
