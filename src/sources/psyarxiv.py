"""
PsyArXiv (OSF) search adapter — psychology and behavioural science preprints.

Purpose: The PsyArXiv provider of the shared OSF adapter.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M31
Tests:   tests/test_adapters.py (PsyArXiv normalisation), tests/test_query_builder.py::test_b2_empty_string_dates_use_days_back_osf
"""

from .osf import OsfPreprintAdapter


class PsyArxivAdapter(OsfPreprintAdapter):
    """Search adapter for PsyArXiv preprints via the OSF API."""

    source_name = "psyarxiv"
    label = "PsyArXiv"
    provider = "psyarxiv"
