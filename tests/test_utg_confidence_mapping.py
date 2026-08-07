# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Property test for usage-type-group confidence mapping.

# Feature: usage-type-aware-ce-multipliers, Property 6: Confidence mapping for usage-type-group sources

For any source string matching the pattern ``"usage_type_group:<suffix>"``,
:func:`dr_cost_estimator_mcp.core.confidence.source_to_confidence`
returns confidence level ``"high"`` and a prose string containing both
the suffix and the service name.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dr_cost_estimator_mcp.core.confidence import source_to_confidence


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Suffix alphabet: printable characters that represent realistic usage-type-group
# suffixes (e.g., "Running Hours", "Storage", "Node Hours"). Includes letters,
# digits, spaces, hyphens, underscores, colons, and periods.
_SUFFIX_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_:."
)

_SERVICE_NAME_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_."
)

_suffix_strategy = st.text(
    alphabet=_SUFFIX_ALPHABET, min_size=1, max_size=60
)

_service_name_strategy = st.text(
    alphabet=_SERVICE_NAME_ALPHABET, min_size=1, max_size=80
).map(str.strip).filter(lambda s: len(s) >= 1)


# ---------------------------------------------------------------------------
# Property 6: usage_type_group:<suffix> → "high" confidence with prose
# ---------------------------------------------------------------------------


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(suffix=_suffix_strategy, service_name=_service_name_strategy)
def test_usage_type_group_source_returns_high_confidence(
    suffix: str, service_name: str
) -> None:
    """For any source string matching ``"usage_type_group:<suffix>"``,
    ``source_to_confidence(source, service_name)`` returns confidence
    level ``"high"`` and a prose string containing both the suffix and
    the service name.

    # Feature: usage-type-aware-ce-multipliers, Property 6: Confidence mapping for usage-type-group sources
    """
    source = f"usage_type_group:{suffix}"

    level, prose = source_to_confidence(source, service_name)

    # 1. Returns confidence level "high"
    assert level == "high", (
        f"source {source!r} with service_name {service_name!r}: "
        f"expected confidence level 'high', got {level!r}"
    )

    # 2. Returns a prose string containing the suffix and service name
    assert isinstance(prose, str), (
        f"source {source!r}: returned prose has type "
        f"{type(prose).__name__}, expected str"
    )
    assert prose.strip(), (
        f"source {source!r} with service_name {service_name!r}: "
        f"returned empty / whitespace-only prose {prose!r}"
    )
    assert suffix in prose, (
        f"source {source!r} with service_name {service_name!r}: "
        f"prose {prose!r} does not contain suffix {suffix!r}"
    )
    assert service_name in prose, (
        f"source {source!r} with service_name {service_name!r}: "
        f"prose {prose!r} does not contain service_name {service_name!r}"
    )
