# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Integration tests with a faked CE client for usage-type-group resolution.

The fake (:class:`tests.utg_fakes.FakeUTGCEClient`) mirrors the REAL
GetCostAndUsage contract: ``USAGE_TYPE_GROUP`` is only valid as a
``Filter`` (a ``GroupBy`` on it raises, like the live API), and detail
queries return per-month ``Total`` blocks.

Test 1: Discovery call + one filtered detail call per configured group
Test 2: Pagination via NextPageToken consumed correctly
Test 3: CE error for one service triggers fallback + warning
Test 4: End-to-end response shape with always-on UTG splitting,
        including the remainder row for unmatched spend
"""

from __future__ import annotations

import asyncio

from dr_cost_estimator_mcp.core.multipliers import utg_groups_for_service
from dr_cost_estimator_mcp.models import AnalyzeDrStrategyResponse
from dr_cost_estimator_mcp.tools import DRCostEstimatorTools

from tests.utg_fakes import FakeUTGCEClient, extract_filter_values

RDS = "Amazon Relational Database Service"
EC2 = "Amazon Elastic Compute Cloud - Compute"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _analyze(client, strategy: str = "warm_standby", months_back: int = 1):
    tools = DRCostEstimatorTools()
    return _run(
        tools.analyze_dr_strategy(
            strategy=strategy,
            months_back=months_back,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=True,
            client=client,
        )
    )


# ---------------------------------------------------------------------------
# Test 1: Discovery call + per-group detail calls issued for RDS
# ---------------------------------------------------------------------------


class TestDiscoveryAndUtgDetailCalls:
    """When RDS is in the portfolio, a discovery GroupBy=[SERVICE] call
    plus one filtered detail call per configured usage type group are
    issued -- and no call ever puts USAGE_TYPE_GROUP in GroupBy.
    """

    def test_discovery_and_per_group_calls_issued_for_rds(self) -> None:
        fake = FakeUTGCEClient(
            portfolio_by_month=[[(RDS, 1000.0), (EC2, 500.0)]],
            group_amounts={
                (RDS, "RDS: Running Hours"): [700.0],
                (RDS, "RDS: Storage"): [200.0],
                (RDS, "Aurora: Backup Usage"): [100.0],
                (RDS, "RDS: I/O Requests"): [0.0],
            },
        )

        response = _analyze(fake)
        assert response.success, f"Tool failed: {response.error}"

        assert len(fake.discovery_calls) >= 1

        # One detail call per configured RDS group, each filtered to the
        # exact (SERVICE, USAGE_TYPE_GROUP) pair.
        detail_groups = [
            extract_filter_values(c.get("Filter"), "USAGE_TYPE_GROUP")[0]
            for c in fake.detail_calls
        ]
        assert sorted(detail_groups) == sorted(utg_groups_for_service(RDS))
        for call in fake.detail_calls:
            assert extract_filter_values(call.get("Filter"), "SERVICE") == [RDS]
            # Detail queries are filter-only: no GroupBy at all.
            assert not call.get("GroupBy"), call.get("GroupBy")


# ---------------------------------------------------------------------------
# Test 2: Pagination via NextPageToken consumed correctly
# ---------------------------------------------------------------------------


class TestPaginationConsumed:
    """Detail queries that paginate month-per-page are aggregated into a
    correct monthly average."""

    def test_pagination_token_consumed(self) -> None:
        # Two months. Running Hours: (400+500)/2 = 450 monthly average;
        # Storage: (300+300)/2 = 300. Service total avg = 750.
        fake = FakeUTGCEClient(
            portfolio_by_month=[
                [(RDS, 700.0), (EC2, 200.0)],
                [(RDS, 800.0), (EC2, 200.0)],
            ],
            group_amounts={
                (RDS, "RDS: Running Hours"): [400.0, 500.0],
                (RDS, "RDS: Storage"): [300.0, 300.0],
            },
            paginate_detail=True,
        )

        response = _analyze(fake, months_back=2)
        assert response.success, f"Tool failed: {response.error}"

        # Each detail query paginates: some calls carry NextPageToken.
        tokened = [c for c in fake.detail_calls if "NextPageToken" in c]
        assert tokened, "expected detail calls carrying NextPageToken"

        rds_rows = [e for e in response.estimates if e.service == RDS]
        assert len(rds_rows) == 1
        # Monthly average across both pages: 450 + 300 = 750.
        assert abs(rds_rows[0].current_cost - 750.0) < 0.01


# ---------------------------------------------------------------------------
# Test 3: CE error for one service triggers fallback + warning
# ---------------------------------------------------------------------------


class TestCeErrorFallbackWithWarning:
    """When detail queries raise, the service falls back to the blended
    service-level multiplier and a warning names it."""

    def test_exception_triggers_fallback_and_warning(self) -> None:
        fake = FakeUTGCEClient(
            portfolio_by_month=[[(RDS, 800.0), (EC2, 400.0)]],
            group_query_behavior="raise",
        )

        response = _analyze(fake, strategy="pilot_light")
        assert response.success, f"Tool should not fail: {response.error}"

        estimate_services = [e.service for e in response.estimates]
        assert RDS in estimate_services
        assert EC2 in estimate_services

        assert response.warnings
        assert any(RDS in w for w in response.warnings), response.warnings


# ---------------------------------------------------------------------------
# Test 4: End-to-end response shape with always-on UTG splitting
# ---------------------------------------------------------------------------


class TestResponseShape:
    """A portfolio mixing a UTG-supported service (RDS) and an
    unsupported one (EC2) produces a valid response where the RDS row is
    resolved via the usage-type-group split, including a remainder row
    for spend no configured group captured.
    """

    def test_response_shape_with_utg_split_and_remainder(self) -> None:
        # RDS service total is 1000 but configured groups only capture
        # 900; the remaining 100 must resolve via the blended service
        # multiplier inside the split (remainder row).
        fake = FakeUTGCEClient(
            portfolio_by_month=[[(RDS, 1000.0), (EC2, 500.0)]],
            group_amounts={
                (RDS, "RDS: Running Hours"): [700.0],
                (RDS, "RDS: Storage"): [200.0],
            },
        )

        response = _analyze(fake)
        assert response.success, f"Tool failed: {response.error}"
        assert isinstance(response, AnalyzeDrStrategyResponse)

        assert response.strategy == "warm_standby"
        assert response.strategy_label is not None
        assert response.totals is not None
        assert response.confidence_summary is not None

        services = {e.service for e in response.estimates}
        assert services == {RDS, EC2}

        for row in response.estimates:
            assert row.calculation.multiplier is not None
            assert row.calculation.dr_additional_cost is not None
            assert row.calculation.total_with_dr is not None
            assert row.confidence
            assert row.confidence_source
            assert row.rationale

        rds_row = next(e for e in response.estimates if e.service == RDS)
        assert rds_row.confidence_source.startswith("usage-type-group split")
        # The remainder preserves the service total exactly.
        assert abs(rds_row.current_cost - 1000.0) < 0.01
        # Effective multiplier: (700*0.5 + 200*1.0 + 100*blended_rds_ws) / 1000
        # blended RDS warm_standby = 0.5 -> (350 + 200 + 50) / 1000 = 0.6
        assert abs(rds_row.calculation.multiplier - 0.6) < 0.005
        # The remainder row drops the service to medium-high confidence
        # (worst-of-constituents), keeping the blended slice visible.
        assert rds_row.confidence == "medium-high"
        # The rationale names the remainder slice.
        assert "(other usage)" in rds_row.rationale
