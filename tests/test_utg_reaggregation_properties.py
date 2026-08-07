# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Property tests for re-aggregation and rationale logic.

Feature: usage-type-aware-ce-multipliers, Properties 7, 8, 9, 10

Tests verify that:
- Property 7: Re-aggregation cost invariant - sums are preserved across
  usage-type-group rows when aggregated to service level.
- Property 8: Effective multiplier derivation - for aggregated rows with
  current_cost > 0, effective_multiplier == dr_additional_cost / current_cost
  within floating-point tolerance.
- Property 9: Lowest-confidence selection - the aggregated row's confidence
  is the minimum (worst) confidence among constituent rows.
- Property 10: Rationale format and length constraint - rationale starts with
  "Split by usage type group:", is ≤300 chars, and names the group with highest
  monthly_cost as the dominant cost driver.
"""

from __future__ import annotations

import math

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from dr_cost_estimator_mcp.tools import (
    _CONFIDENCE_ORDERING,
    _RATIONALE_MAX_CHARS,
    _build_utg_rationale,
    _reaggregate_utg_rows,
)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Confidence levels used by the system
_CONFIDENCE_LEVELS = ["high", "medium-high", "medium", "low"]

# Alphabet for generating realistic service names and usage-type-group names
_SERVICE_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_"
)

_GROUP_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 "
)


def _service_name_strategy() -> st.SearchStrategy[str]:
    """Generate realistic service display names."""
    return st.text(
        alphabet=_SERVICE_ALPHABET, min_size=3, max_size=40
    ).map(str.strip).filter(lambda s: len(s) >= 3)


def _group_name_strategy() -> st.SearchStrategy[str]:
    """Generate realistic usage-type-group suffix names."""
    return st.text(
        alphabet=_GROUP_ALPHABET, min_size=2, max_size=25
    ).map(str.strip).filter(lambda s: len(s) >= 2)


def _positive_cost_strategy() -> st.SearchStrategy[float]:
    """Generate positive cost values suitable for monthly_cost and dr_additional_cost."""
    return st.floats(
        min_value=0.01, max_value=1_000_000.0,
        allow_nan=False, allow_infinity=False,
    )


def _non_negative_cost_strategy() -> st.SearchStrategy[float]:
    """Generate non-negative cost values for dr_additional_cost."""
    return st.floats(
        min_value=0.0, max_value=1_000_000.0,
        allow_nan=False, allow_infinity=False,
    )


def _multiplier_strategy() -> st.SearchStrategy[float]:
    """Generate multiplier values in a realistic range."""
    return st.floats(
        min_value=0.0, max_value=2.0,
        allow_nan=False, allow_infinity=False,
    )


def _confidence_strategy() -> st.SearchStrategy[str]:
    """Draw from the set of valid confidence levels."""
    return st.sampled_from(_CONFIDENCE_LEVELS)


def _utg_row_strategy(service: str | None = None) -> st.SearchStrategy[dict]:
    """Generate a single UTG row dict with all required fields."""
    svc = st.just(service) if service else _service_name_strategy()
    return st.fixed_dictionaries({
        "service": svc,
        "usage_type_group": _group_name_strategy(),
        "monthly_cost": _positive_cost_strategy(),
        "dr_additional_cost": _non_negative_cost_strategy(),
        "multiplier": _multiplier_strategy(),
        "confidence": _confidence_strategy(),
        "source": st.text(min_size=5, max_size=40),
    })


def _utg_rows_for_single_service_strategy() -> st.SearchStrategy[list[dict]]:
    """Generate a list of 1-10 UTG rows all for the same service."""
    return _service_name_strategy().flatmap(
        lambda svc: st.lists(
            _utg_row_strategy(service=svc),
            min_size=1, max_size=10,
        )
    )


# ---------------------------------------------------------------------------
# Property 7: Re-aggregation cost invariant
# ---------------------------------------------------------------------------


# Feature: usage-type-aware-ce-multipliers, Property 7: Re-aggregation cost invariant
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(utg_rows=_utg_rows_for_single_service_strategy())
def test_reaggregation_preserves_cost_sums(utg_rows: list[dict]) -> None:
    """For any set of UTG rows for a single service, the aggregated row's
    monthly_cost equals the sum of input monthly_costs, and dr_additional_cost
    equals the sum of input dr_additional_costs.

    # Feature: usage-type-aware-ce-multipliers, Property 7: Re-aggregation cost invariant
    """
    expected_monthly_cost = sum(r["monthly_cost"] for r in utg_rows)
    expected_dr_additional_cost = sum(r["dr_additional_cost"] for r in utg_rows)

    result = _reaggregate_utg_rows(utg_rows)

    # Should produce exactly one aggregated row (all rows have same service)
    assert len(result) == 1, (
        f"Expected 1 aggregated row for a single service, got {len(result)}"
    )

    row = result[0]

    assert math.isclose(row["monthly_cost"], expected_monthly_cost, rel_tol=1e-9), (
        f"monthly_cost mismatch: expected {expected_monthly_cost}, "
        f"got {row['monthly_cost']}"
    )

    assert math.isclose(row["dr_additional_cost"], expected_dr_additional_cost, rel_tol=1e-9), (
        f"dr_additional_cost mismatch: expected {expected_dr_additional_cost}, "
        f"got {row['dr_additional_cost']}"
    )


# ---------------------------------------------------------------------------
# Property 8: Effective multiplier derivation
# ---------------------------------------------------------------------------


# Feature: usage-type-aware-ce-multipliers, Property 8: Effective multiplier derivation
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(utg_rows=_utg_rows_for_single_service_strategy())
def test_effective_multiplier_equals_dr_cost_over_current_cost(
    utg_rows: list[dict],
) -> None:
    """For aggregated rows with monthly_cost > 0, the effective multiplier
    equals dr_additional_cost / monthly_cost within floating-point tolerance.

    # Feature: usage-type-aware-ce-multipliers, Property 8: Effective multiplier derivation
    """
    total_monthly_cost = sum(r["monthly_cost"] for r in utg_rows)
    total_dr_additional_cost = sum(r["dr_additional_cost"] for r in utg_rows)

    # All rows use _positive_cost_strategy so total_monthly_cost > 0,
    # but guard against edge case.
    assume(total_monthly_cost > 0)

    result = _reaggregate_utg_rows(utg_rows)
    assert len(result) == 1

    row = result[0]
    expected_multiplier = total_dr_additional_cost / total_monthly_cost

    assert math.isclose(
        row["effective_multiplier"], expected_multiplier, rel_tol=1e-9
    ), (
        f"effective_multiplier mismatch: expected {expected_multiplier}, "
        f"got {row['effective_multiplier']}"
    )


# ---------------------------------------------------------------------------
# Property 9: Lowest-confidence selection
# ---------------------------------------------------------------------------


# Feature: usage-type-aware-ce-multipliers, Property 9: Lowest-confidence selection for aggregated rows
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    confidences=st.lists(
        st.sampled_from(_CONFIDENCE_LEVELS),
        min_size=1, max_size=10,
    ),
)
def test_lowest_confidence_selected(confidences: list[str]) -> None:
    """For any non-empty set of confidence levels drawn from
    {"high", "medium-high", "medium", "low"}, the aggregated row's
    confidence is the minimum (worst) confidence according to the ordering
    high > medium-high > medium > low.

    # Feature: usage-type-aware-ce-multipliers, Property 9: Lowest-confidence selection for aggregated rows
    """
    service = "TestService"

    # Build UTG rows with the specified confidence levels
    utg_rows = [
        {
            "service": service,
            "usage_type_group": f"Group{i}",
            "monthly_cost": 100.0,
            "dr_additional_cost": 50.0,
            "multiplier": 0.5,
            "confidence": conf,
            "source": f"usage_type_group:Group{i}",
        }
        for i, conf in enumerate(confidences)
    ]

    result = _reaggregate_utg_rows(utg_rows)
    assert len(result) == 1

    row = result[0]

    # Determine expected minimum confidence: highest index in _CONFIDENCE_ORDERING
    expected_min_confidence = max(
        confidences,
        key=lambda c: _CONFIDENCE_ORDERING.index(c),
    )

    assert row["confidence"] == expected_min_confidence, (
        f"Expected confidence {expected_min_confidence!r} (worst from "
        f"{confidences!r}), got {row['confidence']!r}"
    )


# ---------------------------------------------------------------------------
# Property 10: Rationale format and length constraint
# ---------------------------------------------------------------------------


# Feature: usage-type-aware-ce-multipliers, Property 10: Rationale format and length constraint
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    utg_row_dicts=st.lists(
        st.fixed_dictionaries({
            "usage_type_group": _group_name_strategy(),
            "multiplier": _multiplier_strategy(),
            "monthly_cost": _positive_cost_strategy(),
        }),
        min_size=1, max_size=10,
    ),
)
def test_rationale_format_and_length(utg_row_dicts: list[dict]) -> None:
    """For any usage-type-group split result (1-10 groups, each with a name
    and multiplier), the generated rationale string:
    (a) starts with "Split by usage type group:"
    (b) does not exceed 300 characters
    (c) correctly identifies the group with the highest monthly_cost as the
        dominant cost driver.

    # Feature: usage-type-aware-ce-multipliers, Property 10: Rationale format and length constraint
    """
    rationale = _build_utg_rationale(utg_row_dicts)

    # (a) Starts with the expected prefix
    assert rationale.startswith("Split by usage type group:"), (
        f"Rationale does not start with expected prefix: {rationale!r}"
    )

    # (b) Does not exceed 300 characters
    assert len(rationale) <= _RATIONALE_MAX_CHARS, (
        f"Rationale exceeds {_RATIONALE_MAX_CHARS} chars "
        f"(length={len(rationale)}): {rationale!r}"
    )

    # (c) Correctly identifies the dominant cost driver
    dominant_group = max(utg_row_dicts, key=lambda r: r["monthly_cost"])
    dominant_name = dominant_group["usage_type_group"]

    expected_suffix = f"Dominant cost driver: {dominant_name}."
    assert expected_suffix in rationale, (
        f"Rationale does not contain expected dominant cost driver "
        f"suffix {expected_suffix!r}: {rationale!r}"
    )
