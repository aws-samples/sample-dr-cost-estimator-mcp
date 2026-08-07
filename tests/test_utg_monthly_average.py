# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Property test for monthly average computation.

# Feature: usage-type-aware-ce-multipliers, Property 4: Monthly average computation

For any list of N > 0 monthly cost values, the computed monthly average
SHALL equal ``sum(costs) / N`` within floating-point tolerance.

This property tests the averaging logic that ``_fetch_usage_type_groups``
uses internally: given a list of monthly cost values and one or more
usage-type groups, the per-group average is computed as
``total_for_group / month_count``. We verify this both in isolation
(pure arithmetic) and indirectly via a mocked CE response.
"""

from __future__ import annotations

import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dr_cost_estimator_mcp.core.aws_costs import _fetch_usage_type_groups

from tests.utg_fakes import FakeUTGCEClient


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Strategy for a single positive monthly cost value.
# Use finite positive floats representative of real AWS spend ($0.01 to $1M).
_positive_cost = st.floats(
    min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
)

# Strategy for a non-empty list of monthly costs (1 to 24 months).
_monthly_costs_list = st.lists(
    _positive_cost, min_size=1, max_size=24
)

# Strategy for a configured RDS usage-type-group suffix. The fetcher only
# queries groups configured in usage_type_group_multipliers.json, so the
# generator must draw from that set (full CE value = "RDS: " + suffix).
_group_suffix = st.sampled_from([
    "Running Hours", "Storage", "I/O Requests",
])


# ---------------------------------------------------------------------------
# Property 4: Monthly average computation - pure arithmetic
# ---------------------------------------------------------------------------


# Feature: usage-type-aware-ce-multipliers, Property 4: Monthly average computation
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(costs=_monthly_costs_list)
def test_monthly_average_equals_sum_over_n(costs: list[float]) -> None:
    """For any list of N > 0 monthly costs, verify that the average
    computed as sum(costs) / N equals the expected value within
    floating-point tolerance (epsilon = 1e-9).

    # Feature: usage-type-aware-ce-multipliers, Property 4: Monthly average computation

    This tests the core arithmetic in isolation: the same operation
    performed inside _fetch_usage_type_groups when it divides
    totals_by_group[group] by month_count.
    """
    n = len(costs)
    total = sum(costs)
    expected_average = total / n

    # Replicate the averaging logic from _fetch_usage_type_groups:
    # It accumulates totals_by_group[group] += amount for each month,
    # then divides by month_count.
    accumulated_total = 0.0
    month_count = 0
    for cost in costs:
        accumulated_total += cost
        month_count += 1

    computed_average = accumulated_total / month_count

    # Verify within floating-point tolerance
    assert math.isclose(computed_average, expected_average, rel_tol=1e-9, abs_tol=1e-12), (
        f"Average mismatch: computed {computed_average} vs expected {expected_average} "
        f"for {n} months with total {total}"
    )


# ---------------------------------------------------------------------------
# Property 4: Monthly average via mocked CE response
# ---------------------------------------------------------------------------


# Feature: usage-type-aware-ce-multipliers, Property 4: Monthly average computation
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    monthly_amounts=st.lists(
        _positive_cost, min_size=1, max_size=12
    ),
    group_suffix=_group_suffix,
)
def test_monthly_average_via_mocked_ce_response(
    monthly_amounts: list[float],
    group_suffix: str,
) -> None:
    """For any list of N > 0 monthly cost values and a usage-type group,
    verify that _fetch_usage_type_groups computes the monthly average as
    sum(monthly_amounts) / N within floating-point tolerance.

    # Feature: usage-type-aware-ce-multipliers, Property 4: Monthly average computation

    This test fakes a CE client that answers each per-group filtered
    detail query with one ResultsByTime period per month, each carrying
    the specified cost in its ``Total`` block. The function should
    produce a row for the group whose monthly_cost equals the expected
    average.
    """
    service_name = "Amazon Relational Database Service"
    ce_group_value = f"RDS: {group_suffix}"

    n = len(monthly_amounts)
    expected_average = sum(monthly_amounts) / n

    fake = FakeUTGCEClient(
        # The portfolio drives period count in the fake; service total is
        # the group's own average so no remainder row is emitted.
        portfolio_by_month=[
            [(service_name, amount)] for amount in monthly_amounts
        ],
        group_amounts={(service_name, ce_group_value): list(monthly_amounts)},
    )

    # Call the function under test
    rows = _fetch_usage_type_groups(
        client=fake,
        start="2024-01-01",
        end="2025-01-01",
        service_name=service_name,
        linked_account_ids=None,
        service_monthly_total=expected_average,
    )

    # Exactly one row for the group under test (other configured groups
    # return zero and are dropped; remainder is zero).
    matching = [r for r in rows if r["usage_type_group"] == ce_group_value]
    assert len(matching) == 1, (
        f"Expected 1 row for group {ce_group_value!r}, got rows: {rows}"
    )

    row = matching[0]
    assert row["service"] == service_name

    assert math.isclose(row["monthly_cost"], expected_average, rel_tol=1e-9, abs_tol=1e-12), (
        f"Monthly average mismatch: got {row['monthly_cost']} vs "
        f"expected {expected_average} for {n} months"
    )
