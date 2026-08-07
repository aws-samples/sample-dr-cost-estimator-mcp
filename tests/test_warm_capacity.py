# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for the ``warm_standby_capacity`` parameter.

The parameter rescales the warm-standby multiplier for fleet-shaped
compute entries flagged ``scales_with_warm_capacity`` (EC2 instances,
EBS, ECS, EMR, and the server-compute category). Entries whose warm
posture is NOT a generic fleet percentage - control-plane fees (EKS),
replica-count-driven databases (RDS), storage replication (S3, EFS) -
must ignore it, as must every other strategy.
"""

from __future__ import annotations

import asyncio

import pytest

from dr_cost_estimator_mcp.core.multipliers import (
    DEFAULT_WARM_CAPACITY,
    KNOWN_WARM_SCALING,
    SERVICE_CATEGORIES,
    STRATEGIES,
    get_multiplier,
    get_rationale,
)
from dr_cost_estimator_mcp.tools import DRCostEstimatorTools

from tests.utg_fakes import FakeUTGCEClient


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Resolution-level behavior
# ---------------------------------------------------------------------------


class TestScaledEntries:
    def test_flagged_entries_are_exactly_the_fleet_shaped_set(self):
        assert KNOWN_WARM_SCALING == {
            "ec2-instances",
            "amazon elastic compute cloud - compute",
            "ec2-other",
            "ec2 - other",
            "elastic container service",
            "ecs",
            "elastic mapreduce",
            "emr",
        }

    def test_flagged_entries_scale_linearly(self):
        for capacity, expected in [(0.1, 0.1), (0.25, 0.25), (0.5, 0.5), (1.0, 1.0)]:
            value, source = get_multiplier(
                "EC2 - Other", "warm_standby", capacity
            )
            assert source == "known"
            assert value == pytest.approx(expected)

    def test_flagged_entry_active_active_never_exceeded(self):
        """Scaled warm standby stays <= active/active at maximum capacity.

        Every flagged entry has warm=0.5 and active/active=1.0, so the
        scaled warm value tops out exactly at the active/active value.
        """
        for service in KNOWN_WARM_SCALING:
            warm, _ = get_multiplier(service, "warm_standby", 1.0)
            aa, _ = get_multiplier(service, "active_active")
            assert warm <= aa, service

    def test_compute_server_category_scales(self):
        # AWS Batch has no curated entry and resolves via compute_server.
        value, source = get_multiplier("AWS Batch", "warm_standby", 0.25)
        assert source == "category:compute_server"
        assert value == pytest.approx(0.25)

    def test_other_strategies_ignore_the_parameter(self):
        for strategy in STRATEGIES:
            if strategy == "warm_standby":
                continue
            default_value, _ = get_multiplier("ec2-instances", strategy)
            scaled_value, _ = get_multiplier("ec2-instances", strategy, 0.25)
            assert scaled_value == default_value, strategy


class TestUnscaledEntries:
    @pytest.mark.parametrize(
        "service,expected",
        [
            ("Amazon Relational Database Service", 0.5),  # replica count
            ("Amazon Elastic Kubernetes Service", 1.0),  # control plane fee
            ("Amazon Simple Storage Service", 1.0),  # storage replication
            ("elastic file system", 1.0),
            ("elasticache", 0.65),
            ("opensearch", 0.7),
            ("redshift", 0.5),  # cluster sizing, not an auto-scaling fleet
        ],
    )
    def test_unflagged_entries_ignore_the_parameter(self, service, expected):
        value, _ = get_multiplier(service, "warm_standby", 0.25)
        assert value == pytest.approx(expected)

    def test_data_category_ignores_the_parameter(self):
        value, source = get_multiplier("Amazon Timestream", "warm_standby", 0.25)
        assert source == "category:data"
        assert value == SERVICE_CATEGORIES["data"]["defaults"]["warm_standby"]

    def test_default_tier_ignores_the_parameter(self):
        value, source = get_multiplier(
            "Some Brand New Offering", "warm_standby", 0.25
        )
        assert source == "default"
        default_value, _ = get_multiplier("Some Brand New Offering", "warm_standby")
        assert value == default_value


class TestRationaleSuffix:
    def test_suffix_appended_for_scaled_entry_at_non_default_capacity(self):
        rationale = get_rationale("ec2-instances", "warm_standby", 0.25)
        assert "25%" in rationale
        assert "warm_standby_capacity" in rationale

    def test_no_suffix_at_default_capacity(self):
        rationale = get_rationale(
            "ec2-instances", "warm_standby", DEFAULT_WARM_CAPACITY
        )
        assert "warm_standby_capacity" not in rationale

    def test_no_suffix_for_unscaled_entry(self):
        rationale = get_rationale(
            "Amazon Relational Database Service", "warm_standby", 0.25
        )
        assert "warm_standby_capacity" not in rationale

    def test_no_suffix_for_other_strategies(self):
        rationale = get_rationale("ec2-instances", "active_active", 0.25)
        assert "warm_standby_capacity" not in rationale


# ---------------------------------------------------------------------------
# Tool-level behavior
# ---------------------------------------------------------------------------

EC2 = "Amazon Elastic Compute Cloud - Compute"
RDS = "Amazon Relational Database Service"


def _fake() -> FakeUTGCEClient:
    return FakeUTGCEClient(
        portfolio_by_month=[[(EC2, 1000.0), (RDS, 400.0)]],
        group_amounts={
            (RDS, "RDS: Running Hours"): [300.0],
            (RDS, "RDS: Storage"): [100.0],
        },
    )


def _analyze(warm_standby_capacity: float, strategy: str = "warm_standby"):
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
            warm_standby_capacity=warm_standby_capacity,
            client=_fake(),
        )
    )


class TestAnalyzeToolWithWarmCapacity:
    def test_scaled_service_row_and_warning(self):
        response = _analyze(0.25)
        assert response.success, response.error

        rows = {r.service: r for r in response.estimates}
        # EC2 scaled: 0.5 * (0.25 / 0.5) = 0.25.
        assert rows[EC2].calculation.multiplier == pytest.approx(0.25)
        assert "25%" in rows[EC2].rationale
        # RDS unaffected: UTG split (300*0.5 + 100*1.0) / 400 = 0.625.
        assert rows[RDS].calculation.multiplier == pytest.approx(0.625)

        assert any("warm_standby_capacity=0.25" in w for w in response.warnings)

    def test_default_capacity_appends_no_warning(self):
        response = _analyze(DEFAULT_WARM_CAPACITY)
        assert response.success, response.error
        assert not any("warm_standby_capacity" in w for w in response.warnings)
        rows = {r.service: r for r in response.estimates}
        assert rows[EC2].calculation.multiplier == pytest.approx(0.5)

    @pytest.mark.parametrize("bad", [0.0, 0.09, 1.01, -1.0])
    def test_out_of_range_rejected(self, bad):
        response = _analyze(bad)
        assert response.success is False
        assert response.error is not None
        assert response.error.code == "INVALID_INPUT"
        assert "warm_standby_capacity" in response.error.message

    def test_non_warm_strategy_unaffected_by_parameter(self):
        response = _analyze(0.25, strategy="active_active")
        assert response.success, response.error
        rows = {r.service: r for r in response.estimates}
        assert rows[EC2].calculation.multiplier == pytest.approx(1.0)


class TestCompareToolWithWarmCapacity:
    def test_only_warm_standby_column_scales(self):
        tools = DRCostEstimatorTools()
        response = _run(
            tools.compare_dr_strategies(
                months_back=1,
                linked_account_ids=None,
                profile=None,
                region=None,
                top_n=25,
                include_all_services=True,
                warm_standby_capacity=0.25,
                client=_fake(),
            )
        )
        assert response.success, response.error

        ec2_row = next(s for s in response.top_services if s.service == EC2)
        assert ec2_row.per_strategy["warm_standby"].multiplier == pytest.approx(0.25)
        assert ec2_row.per_strategy["active_active"].multiplier == pytest.approx(1.0)
        assert ec2_row.per_strategy["pilot_light"].multiplier == pytest.approx(0.0)
        assert any("warm_standby_capacity=0.25" in w for w in response.warnings)

    def test_compare_rejects_out_of_range(self):
        tools = DRCostEstimatorTools()
        response = _run(
            tools.compare_dr_strategies(
                months_back=1,
                linked_account_ids=None,
                profile=None,
                region=None,
                top_n=25,
                include_all_services=True,
                warm_standby_capacity=2.0,
                client=_fake(),
            )
        )
        assert response.success is False
        assert response.error.code == "INVALID_INPUT"
