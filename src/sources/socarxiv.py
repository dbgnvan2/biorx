"""
SocArXiv (OSF) search adapter — social science preprints.

Purpose: The SocArXiv provider of the shared OSF adapter.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M31
Tests:   tests/test_batch_g.py (SocArXiv normalisation)
"""

from .osf import OsfPreprintAdapter


class SocArxivAdapter(OsfPreprintAdapter):
    """Search adapter for SocArXiv preprints via the OSF API."""

    source_name = "socarxiv"
    label = "SocArXiv"
    provider = "socarxiv"
