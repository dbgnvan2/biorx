"""
Batch E — tests that check the wrong thing.

Items:
  E1 (GL.3 pre-empt): test_the_access_code_is_compared_in_constant_time deleted
     from tests/web/test_auth.py. It asserted via bytecode names (source-text
     assertion, P19 corollary / P32: the test agreed with the implementation, not
     with a citable rule). GL.3 removes the access code entirely; fixing a
     behavioural replacement now would be deleted with it a few commits later.

  E2: test_e_env_example_covers_config_declared_variables — the existing
     test_env_example_documents_every_variable_the_code_reads (test_deploy_files.py)
     collects env var names by grepping for os.environ.get("NAME") literals.
     Variables read through indirection (provider api_key_env / model_env in
     llm_config.yaml) escape it because the string argument is a variable, not
     a literal. This test covers the gap (P25 / the "build but not wired" corollary
     applied to documentation coverage).
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

LLM_CONFIG = ROOT / "llm_config.yaml"
ENV_EXAMPLE = ROOT / ".env.example"


def _yaml_declared_env_vars() -> set[str]:
    """
    Collect every value stored under api_key_env or model_env in llm_config.yaml.
    Uses yaml.safe_load for correctness: a bare-text scan would hit comments
    and the key names themselves (P19 corollary — test what the parser sees).
    """
    yaml = pytest.importorskip("yaml")
    raw = yaml.safe_load(LLM_CONFIG.read_text(encoding="utf-8"))
    names = set()
    for provider_cfg in (raw.get("providers") or {}).values():
        if not isinstance(provider_cfg, dict):
            continue
        for field in ("api_key_env", "model_env"):
            val = provider_cfg.get(field, "")
            if val and isinstance(val, str):
                names.add(val)
    return names


def _documented_in_env_example() -> set[str]:
    """Names documented in .env.example, including commented-out entries."""
    return set(
        re.findall(r"^#?\s*([A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"),
                   re.MULTILINE)
    )


def test_e_env_example_covers_config_declared_variables():
    """
    Every env var named in llm_config.yaml via api_key_env or model_env must
    appear in .env.example, so operators know what to set when adding a provider.

    Mutation proof: remove ANTHROPIC_API_KEY= from .env.example → this test fails
    with "undocumented: ['ANTHROPIC_API_KEY']". Restore to fix.

    The existing test_env_example_documents_every_variable_the_code_reads in
    test_deploy_files.py does NOT catch these because os.environ.get(self.api_key_env)
    uses a variable, not a string literal, so regex over source misses it.
    """
    declared = _yaml_declared_env_vars()
    documented = _documented_in_env_example()
    undocumented = sorted(declared - documented)
    assert undocumented == [], (
        f"env vars declared in llm_config.yaml but absent from .env.example: "
        f"{undocumented}. Add a commented-out example line for each."
    )


def test_e_the_guard_would_notice_a_new_undocumented_provider_key(tmp_path):
    """
    Mutation-proves the guard: an llm_config with a new api_key_env not in
    .env.example is caught (P27 — the test above has no assertion to invert,
    so this companion test proves it via an injected bad config).
    """
    yaml = pytest.importorskip("yaml")

    bad_config = {
        "providers": {
            "newco": {
                "dialect": "openai",
                "api_key_env": "NEWCO_API_KEY",
                "model_env": "NEWCO_MODEL",
            }
        }
    }
    bad_yaml = tmp_path / "llm_config_bad.yaml"
    bad_yaml.write_text(yaml.dump(bad_config), encoding="utf-8")

    raw = yaml.safe_load(bad_yaml.read_text(encoding="utf-8"))
    names = set()
    for provider_cfg in (raw.get("providers") or {}).values():
        if not isinstance(provider_cfg, dict):
            continue
        for field in ("api_key_env", "model_env"):
            val = provider_cfg.get(field, "")
            if val:
                names.add(val)

    documented = _documented_in_env_example()
    undocumented = sorted(names - documented)
    assert undocumented == ["NEWCO_API_KEY", "NEWCO_MODEL"], (
        f"expected guard to catch NEWCO_API_KEY and NEWCO_MODEL; got: {undocumented}"
    )
