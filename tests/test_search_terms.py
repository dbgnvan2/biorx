"""
The AND operator inside one search term.

Spec:  docs/implementation_plan_2026-10-07_and_terms.md#AND1
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.search_terms import and_parts


@pytest.mark.parametrize("term, parts", [
    ("cooperati* AND survival", ["cooperati*", "survival"]),
    ("a AND b AND c", ["a", "b", "c"]),
    ("kin selection AND survival", ["kin selection", "survival"]),
    ("  a   AND   b  ", ["a", "b"]),
    ("AND survival", ["survival"]),            # no space before: still a leading AND
    ("survival AND", ["survival"]),
    ("a AND AND b", ["a", "b"]),
    ("AND", []),
    ("", []),
    ("anxiety and depression", ["anxiety and depression"]),   # lowercase: a phrase
    ("Rock And Roll", ["Rock And Roll"]),
    ("CD4+ T cells", ["CD4+ T cells"]),
    ("BRANDING", ["BRANDING"]),               # AND inside a word
    ("ANDROGEN AND estrogen", ["ANDROGEN", "estrogen"]),
])
def test_and1_and_parts(term, parts):
    assert and_parts(term) == parts
