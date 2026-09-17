"""
Tests for settings read/write routes.
Spec:    docs/web_parity_spec_2026-09-17.md#FP3-B
Tests:   tests/web/test_settings_routes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest


def test_get_known_file_returns_content(signed_in, tmp_path):
    """GET /api/settings/{filename} returns the file's YAML text."""
    # Write a known file in the config dir location
    from web.routes_settings import _CONFIG_DIR
    p = _CONFIG_DIR / "sources_config.yaml"
    original = p.read_text() if p.exists() else None
    try:
        p.write_text("# test\nfoo: bar\n")
        r = signed_in.get("/api/settings/sources_config.yaml")
        assert r.status_code == 200
        assert "foo: bar" in r.json()["content"]
    finally:
        if original is not None:
            p.write_text(original)
        elif p.exists():
            p.unlink()


def test_get_missing_file_returns_empty_string(signed_in, tmp_path, monkeypatch):
    """A config file that does not exist yet returns "" not 404."""
    # Patch _CONFIG_DIR to a temp directory where neither file exists
    import web.routes_settings as rs
    monkeypatch.setattr(rs, "_CONFIG_DIR", tmp_path)
    r = signed_in.get("/api/settings/llm_config.yaml")
    assert r.status_code == 200
    assert r.json()["content"] == ""


def test_unknown_filename_is_rejected(signed_in):
    r = signed_in.get("/api/settings/passwords.txt")
    assert r.status_code == 403


def test_path_traversal_is_rejected(signed_in):
    r = signed_in.get("/api/settings/../../../etc/passwd")
    # FastAPI will URL-decode the path; the route handler rejects ".."
    assert r.status_code in (403, 404, 422)


def test_put_valid_yaml_saves_file(signed_in, tmp_path, monkeypatch):
    import web.routes_settings as rs
    monkeypatch.setattr(rs, "_CONFIG_DIR", tmp_path)
    content = "sources:\n  europepmc:\n    enabled: true\n"
    r = signed_in.put("/api/settings/sources_config.yaml",
                      json={"content": content})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert (tmp_path / "sources_config.yaml").read_text() == content


def test_put_invalid_yaml_is_rejected_before_write(signed_in, tmp_path, monkeypatch):
    import web.routes_settings as rs
    monkeypatch.setattr(rs, "_CONFIG_DIR", tmp_path)
    bad_yaml = "key: [unclosed"
    r = signed_in.put("/api/settings/sources_config.yaml", json={"content": bad_yaml})
    assert r.status_code == 422
    assert "YAML" in r.json()["detail"]
    assert not (tmp_path / "sources_config.yaml").exists()


def test_put_unknown_filename_is_rejected(signed_in):
    r = signed_in.put("/api/settings/secrets.yaml", json={"content": "x: 1"})
    assert r.status_code == 403


def test_settings_routes_require_auth(client):
    assert client.get("/api/settings/sources_config.yaml").status_code == 401
    assert client.put("/api/settings/sources_config.yaml",
                      json={"content": ""}).status_code == 401
