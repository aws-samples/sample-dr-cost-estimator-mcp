# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for usage-type-group multiplier values and parameter surface.

Validates:
- the removed use_usage_type_groups flag stays off the AWS tools
  (usage-type-group splitting is always-on, with no opt-out)
- the data file loads with the correct service keys and groups
- group keys are EXACT Cost Explorer USAGE_TYPE_GROUP dimension values
  (verified against the live API; the API only accepts USAGE_TYPE_GROUP
  as a Filter, and filters match verbatim strings)
- RDS, OpenSearch, and ELB multiplier values
"""

from __future__ import annotations

import inspect

from dr_cost_estimator_mcp.core.multipliers import (
    STRATEGIES,
    SUPPORTED_UTG_SERVICES,
    USAGE_TYPE_GROUP_MULTIPLIERS,
    UTG_REMAINDER_GROUP,
    utg_groups_for_service,
)
from dr_cost_estimator_mcp.tools import DRCostEstimatorTools


# ---------------------------------------------------------------------------
# Test 1: USAGE_TYPE_GROUP_MULTIPLIERS contains the supported service keys
# ---------------------------------------------------------------------------


class TestUTGDataFileLoads:
    """Verify usage_type_group_multipliers.json loads with correct top-level keys."""

    def test_contains_rds_key(self):
        assert "Amazon Relational Database Service" in USAGE_TYPE_GROUP_MULTIPLIERS

    def test_contains_opensearch_key(self):
        assert "Amazon OpenSearch Service" in USAGE_TYPE_GROUP_MULTIPLIERS

    def test_contains_elb_key(self):
        assert "Amazon Elastic Load Balancing" in USAGE_TYPE_GROUP_MULTIPLIERS

    def test_redshift_not_in_utg_table(self):
        """Redshift resolves at the blended service tier: its only
        live-verifiable usage type group ("Redshift: Serverless") does not
        separate compute from storage, so a split adds no signal."""
        assert "Amazon Redshift" not in USAGE_TYPE_GROUP_MULTIPLIERS

    def test_supported_services_derived_from_data(self):
        assert SUPPORTED_UTG_SERVICES == set(USAGE_TYPE_GROUP_MULTIPLIERS)

    def test_remainder_group_never_configured(self):
        """The synthetic remainder label must not appear as a configured
        group; it must always fall through to the blended multiplier."""
        for service, groups in USAGE_TYPE_GROUP_MULTIPLIERS.items():
            assert UTG_REMAINDER_GROUP not in groups, service


# ---------------------------------------------------------------------------
# Test 2: Group keys are exact CE USAGE_TYPE_GROUP dimension values
# ---------------------------------------------------------------------------


class TestGroupKeysAreLiveCEValues:
    """Group keys must be the verbatim strings Cost Explorer reports.

    These were verified against the live GetDimensionValues API. Filters
    match exact strings, so a drifted key silently matches nothing.
    """

    def test_rds_groups(self):
        assert set(utg_groups_for_service("Amazon Relational Database Service")) == {
            "RDS: Running Hours",
            "RDS: Storage",
            "Aurora: Backup Usage",
            "RDS: I/O Requests",
        }

    def test_opensearch_groups(self):
        assert set(utg_groups_for_service("Amazon OpenSearch Service")) == {
            "OpenSearchService: Running Hours",
        }

    def test_elb_groups(self):
        assert set(utg_groups_for_service("Amazon Elastic Load Balancing")) == {
            "EC2: ELB - Running Hours",
            "EC2: ELB - LCU Running Hours",
        }

    def test_unsupported_service_has_no_groups(self):
        assert utg_groups_for_service("Amazon Redshift") == []
        assert utg_groups_for_service("AWS Lambda") == []


# ---------------------------------------------------------------------------
# Test 3: Specific multiplier value assertions
# ---------------------------------------------------------------------------


class TestRDSMultiplierValues:
    """Assert RDS per-group multiplier values."""

    _rds = USAGE_TYPE_GROUP_MULTIPLIERS["Amazon Relational Database Service"]

    def test_running_hours(self):
        assert self._rds["RDS: Running Hours"] == {
            "backup_restore": 0.0,
            "pilot_light": 0.5,
            "warm_standby": 0.5,
            "active_active": 1.0,
        }

    def test_storage(self):
        assert self._rds["RDS: Storage"] == {
            "backup_restore": 1.0,
            "pilot_light": 1.0,
            "warm_standby": 1.0,
            "active_active": 1.0,
        }

    def test_backup_usage(self):
        assert self._rds["Aurora: Backup Usage"] == {
            "backup_restore": 1.0,
            "pilot_light": 1.0,
            "warm_standby": 1.0,
            "active_active": 1.0,
        }

    def test_io_requests(self):
        assert self._rds["RDS: I/O Requests"] == {
            "backup_restore": 0.0,
            "pilot_light": 0.5,
            "warm_standby": 0.5,
            "active_active": 1.0,
        }

    def test_no_data_transfer_groups(self):
        """Cross-region transfer is out of model by policy; no transfer
        group may carry a multiplier. Transfer spend lands in the
        remainder row at the blended service multiplier."""
        for group in self._rds:
            assert "Data Transfer" not in group, group


class TestOpenSearchMultiplierValues:
    """Assert OpenSearch per-group multiplier values."""

    _opensearch = USAGE_TYPE_GROUP_MULTIPLIERS["Amazon OpenSearch Service"]

    def test_running_hours(self):
        assert self._opensearch["OpenSearchService: Running Hours"] == {
            "backup_restore": 0.0,
            "pilot_light": 0.3,
            "warm_standby": 0.7,
            "active_active": 1.0,
        }


class TestELBMultiplierValues:
    """Assert ELB per-group multiplier values.

    Hours: a second load balancer runs in DR from pilot light up, so the
    hours line doubles. LCU: capacity units are traffic-driven; standby
    postures serve no traffic and active/active splits traffic without
    increasing the total, so the ADDITIONAL cost is zero everywhere.
    """

    _elb = USAGE_TYPE_GROUP_MULTIPLIERS["Amazon Elastic Load Balancing"]

    def test_running_hours(self):
        assert self._elb["EC2: ELB - Running Hours"] == {
            "backup_restore": 0.0,
            "pilot_light": 1.0,
            "warm_standby": 1.0,
            "active_active": 1.0,
        }

    def test_lcu_running_hours(self):
        assert self._elb["EC2: ELB - LCU Running Hours"] == {
            "backup_restore": 0.0,
            "pilot_light": 0.0,
            "warm_standby": 0.0,
            "active_active": 0.0,
        }


# ---------------------------------------------------------------------------
# Test 4: Every group entry carries all four strategies
# ---------------------------------------------------------------------------


class TestGroupEntriesComplete:
    def test_all_groups_have_all_strategies(self):
        for service, groups in USAGE_TYPE_GROUP_MULTIPLIERS.items():
            for group, mults in groups.items():
                assert set(mults) == set(STRATEGIES), f"{service} / {group}"


# ---------------------------------------------------------------------------
# Test 5: AWS tools do NOT expose use_usage_type_groups (splitting is always-on)
# ---------------------------------------------------------------------------


class TestAWSToolsDoNotExposeUTGParameter:
    """Verify the removed use_usage_type_groups flag stays off the AWS tools."""

    def test_compare_dr_strategies_has_no_utg_param(self):
        sig = inspect.signature(DRCostEstimatorTools.compare_dr_strategies)
        assert "use_usage_type_groups" not in sig.parameters

    def test_analyze_dr_strategy_has_no_utg_param(self):
        sig = inspect.signature(DRCostEstimatorTools.analyze_dr_strategy)
        assert "use_usage_type_groups" not in sig.parameters
