"""
One admission and settlement path for billed model calls (src/spend.py).

Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M12
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import spend, user_store
from src.tokens import UNCOUNTED, TokenUsage

ROOT = Path(__file__).parent.parent.parent


def test_m12_single_settlement_path():
    """Only src/spend.py records spend or gives a slot back."""
    for path in (ROOT / "web").glob("*.py"):
        text = path.read_text()
        assert "record_spend(" not in text and "release_usage(" not in text, path.name


def _resolved():
    return SimpleNamespace(provider="deepseek", model="m", key_source="owner",
                           billed_to_owner=True)


def test_m12_billed_call_releases_when_the_model_was_never_called():
    db = MagicMock()
    job = SimpleNamespace(token_usage=UNCOUNTED)
    with patch.object(user_store, "release_usage") as released, \
         patch.object(user_store, "record_spend") as recorded:
        with pytest.raises(RuntimeError):
            with spend.BilledCall(db, "u", _resolved(), 42, job):
                raise RuntimeError("failed before the provider")
    released.assert_called_once_with(db, 42)
    recorded.assert_not_called()
    db.release.assert_called_once()


def test_m12_billed_call_records_the_cost_an_exception_carries():
    db = MagicMock()
    job = SimpleNamespace(token_usage=UNCOUNTED)
    billed = TokenUsage(prompt=100, completion=5, counted=True)
    err = RuntimeError("unusable reply")
    err.usage = billed
    with patch.object(user_store, "release_usage") as released, \
         patch.object(user_store, "record_spend") as recorded:
        with pytest.raises(RuntimeError):
            with spend.BilledCall(db, "u", _resolved(), 42, job) as call:
                call.model_called()
                raise err
    released.assert_not_called()
    assert recorded.call_args.args[-1] is billed
    db.release.assert_called_once()


@pytest.mark.parametrize("route,body", [
    ("/api/summaries", {"paper": {"doi": "10.1/x", "title": "t", "abstract": "a"}}),
    ("/api/discover-terms", {"description": "sleep apnea"}),
    ("/api/reviews", None),
])
def test_m12_routes_agree_on_status_codes(signed_in, ctx, monkeypatch, route, body):
    """No usable key: every billed route answers 400 with the same reason."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()
    if body is None:
        lst = signed_in.post("/api/references", json={"name": "L"}).json()
        body = {"list_id": lst["id"]}
    r = signed_in.post(route, json=body)
    assert r.status_code == 400, (route, r.text)
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]
