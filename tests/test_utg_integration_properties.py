# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Property tests for always-on splitting, graceful fallback, and confidence
summary aggregation with usage-type-group resolution.

Tests verify that:
- Property 1: Always-on splitting - per-group filtered detail queries are
  always issued for supported services; there is no opt-out flag.
- Property 11: Graceful fallback on CE query failure - blended multiplier
  used and warning appended when a supported service's detail queries
  return empty or raise.
- Property 12: Confidence summary aggregation for UTG rows - UTG-resolved
  dollars appear in ``total_cost_high_confidence``.
"""

from __future__ import annotations

import asyncio
import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dr_cost_estimator_mcp.core.multipliers import (
    SUPPORTED_UTG_SERVICES,
    utg_groups_for_service,
)
from dr_cost_estimator_mcp.tools import DRCostEstimatorTools

from tests.utg_fakes import FakeUTGCEClient, extract_filter_values

RDS = "Amazon Relational Database Service"
EC2 = "Amazon Elastic Compute Cloud - Compute"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _analyze(client, strategy: str = "warm_standby"):
    tools = DRCostEstimatorTools()
    return _run(
        tools.analyze_dr_strategy(
            strategy=strategy,
            months_back=1,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=True,
            client=client,
        )
    )


# ---------------------------------------------------------------------------
# Property 1: Usage-type-group splitting is always-on
# ---------------------------------------------------------------------------


_unsupported_service_names = st.sampled_from([
    "Amazon Simple Storage Service",
    "Amazon Elastic Compute Cloud - Compute",
    "AmazonCloudWatch",
    "AWS Lambda",
    "Amazon DynamoDB",
])

_supported_service_names = st.sampled_from(sorted(SUPPORTED_UTG_SERVICES))

_monthly_cost = st.floats(
    min_value=10.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)


@st.composite
def _service_portfolio_strategy(draw: st.DrawFn) -> list[tuple[str, float]]:
    """Generate a portfolio of 1-6 services with costs.

    Includes at least one unsupported service plus 1-2 supported
    services, so the always-on splitting path always has at least one
    supported service to issue detail queries for.
    """
    unsupported_count = draw(st.integers(min_value=1, max_value=3))
    unsupported = [
        (draw(_unsupported_service_names), draw(_monthly_cost))
        for _ in range(unsupported_count)
    ]

    supported_count = draw(st.integers(min_value=1, max_value=2))
    supported = [
        (draw(_supported_service_names), draw(_monthly_cost))
        for _ in range(supported_count)
    ]

    seen: set[str] = set()
    portfolio: list[tuple[str, float]] = []
    for name, cost in unsupported + supported:
        if name not in seen:
            seen.add(name)
            portfolio.append((name, cost))

    return portfolio


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(portfolio=_service_portfolio_strategy())
def test_utg_split_always_happens_for_supported_services(
    portfolio: list[tuple[str, float]],
) -> None:
    """Per-group detail queries are always issued for every supported
    service in the portfolio - there is no flag to pass and no opt-out.
    The resulting rows resolve through the usage-type-group tier.
    """
    supported_in_portfolio = {
        name for name, _ in portfolio if name in SUPPORTED_UTG_SERVICES
    }

    # Give the first configured group of every supported service the
    # full service cost so the split has spend to work with.
    group_amounts = {}
    for name, cost in portfolio:
        if name in supported_in_portfolio:
            first_group = utg_groups_for_service(name)[0]
            group_amounts[(name, first_group)] = [cost]

    fake = FakeUTGCEClient(
        portfolio_by_month=[portfolio],
        group_amounts=group_amounts,
    )

    response = _analyze(fake)
    assert response.success, f"Tool failed: {response.error}"

    # Every supported service in the portfolio got detail queries, one
    # per configured group.
    queried_services = {
        extract_filter_values(c.get("Filter"), "SERVICE")[0]
        for c in fake.detail_calls
    }
    assert queried_services == supported_in_portfolio

    for service_name in supported_in_portfolio:
        expected_groups = set(utg_groups_for_service(service_name))
        actual_groups = {
            extract_filter_values(c.get("Filter"), "USAGE_TYPE_GROUP")[0]
            for c in fake.detail_calls
            if extract_filter_values(c.get("Filter"), "SERVICE") == [service_name]
        }
        assert actual_groups == expected_groups, (
            f"{service_name}: queried {actual_groups}, expected {expected_groups}"
        )

    # Supported services resolve through the usage-type-group tier.
    rows_by_service = {row.service: row for row in response.estimates}
    for service_name in supported_in_portfolio:
        row = rows_by_service[service_name]
        assert row.confidence_source.startswith("usage-type-group split"), (
            f"{service_name!r} resolved via {row.confidence_source!r}, "
            "expected the usage-type-group tier"
        )


# ---------------------------------------------------------------------------
# Property 11: Graceful fallback on CE query failure
# ---------------------------------------------------------------------------


def test_graceful_fallback_on_utg_query_failure() -> None:
    """When a supported service's detail queries return empty (simulating
    failure), the service still appears in results using the blended
    multiplier, and a warning is appended naming the affected service.
    """
    fake = FakeUTGCEClient(
        portfolio_by_month=[[(RDS, 500.0), (EC2, 300.0)]],
        group_query_behavior="empty",
    )

    response = _analyze(fake)
    assert response.success, f"Tool failed: {response.error}"

    estimate_services = [e.service for e in response.estimates]
    assert RDS in estimate_services
    assert EC2 in estimate_services

    assert response.warnings
    assert any(RDS in w for w in response.warnings), response.warnings


def test_graceful_fallback_does_not_fail_entire_invocation() -> None:
    """Even when ALL supported services' detail queries fail, the tool
    invocation succeeds and returns results using blended multipliers.
    """
    opensearch = "Amazon OpenSearch Service"
    fake = FakeUTGCEClient(
        portfolio_by_month=[
            [(RDS, 1000.0), (opensearch, 500.0), (EC2, 800.0)]
        ],
        group_query_behavior="raise",
    )

    response = _analyze(fake, strategy="pilot_light")
    assert response.success, f"Tool should not fail: {response.error}"

    estimate_services = {e.service for e in response.estimates}
    assert {RDS, opensearch, EC2} <= estimate_services

    assert response.warnings is not None
    assert len(response.warnings) >= 2, response.warnings


# ---------------------------------------------------------------------------
# Property 12: Confidence summary aggregation for UTG rows
# ---------------------------------------------------------------------------


def test_confidence_summary_includes_utg_resolved_dollars() -> None:
    """When UTG rows are successfully resolved, their costs appear in the
    confidence summary's ``total_cost_high_confidence`` field.
    """
    rds_running_hours_cost = 800.0
    rds_storage_cost = 200.0
    ec2_cost = 500.0
    rds_total = rds_running_hours_cost + rds_storage_cost

    fake = FakeUTGCEClient(
        portfolio_by_month=[[(RDS, rds_total), (EC2, ec2_cost)]],
        group_amounts={
            (RDS, "RDS: Running Hours"): [rds_running_hours_cost],
            (RDS, "RDS: Storage"): [rds_storage_cost],
        },
    )

    response = _analyze(fake)
    assert response.success, f"Tool failed: {response.error}"

    cs = response.confidence_summary
    assert cs is not None

    # Both RDS groups matched configured entries and there is no
    # remainder, so the full RDS cost is high-confidence.
    assert cs.total_cost_high_confidence >= rds_total - 0.01, (
        f"Expected total_cost_high_confidence >= {rds_total}, "
        f"got {cs.total_cost_high_confidence}"
    )

    total_portfolio = rds_total + ec2_cost
    expected_min_percent = (rds_total / total_portfolio) * 100.0
    assert cs.percent_cost_high_confidence >= expected_min_percent - 1.0


def test_confidence_summary_utg_rows_classified_as_high() -> None:
    """A portfolio that is 100% UTG-resolved reports ~100% of dollars at
    high confidence, confirming the integration between the UTG
    resolution path and the confidence summary builder.
    """
    rds_running_hours_cost = 600.0
    rds_storage_cost = 400.0
    rds_total = rds_running_hours_cost + rds_storage_cost

    fake = FakeUTGCEClient(
        portfolio_by_month=[[(RDS, rds_total)]],
        group_amounts={
            (RDS, "RDS: Running Hours"): [rds_running_hours_cost],
            (RDS, "RDS: Storage"): [rds_storage_cost],
        },
    )

    response = _analyze(fake, strategy="backup_restore")
    assert response.success, f"Tool failed: {response.error}"

    cs = response.confidence_summary
    assert cs is not None

    assert math.isclose(
        cs.total_cost_high_confidence, rds_total, rel_tol=1e-2
    )
    assert cs.percent_cost_high_confidence >= 99.0
