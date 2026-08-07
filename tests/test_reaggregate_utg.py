"""Unit tests for _reaggregate_utg_rows in tools.py.
"""

import pytest

from dr_cost_estimator_mcp.tools import _reaggregate_utg_rows


class TestReaggregateUtgRows:
    """Tests for re-aggregation of per-usage-type-group rows to service level."""

    def test_sums_monthly_cost_across_groups(self):
        """monthly_cost is summed across UTG rows for a service."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 500.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Storage",
                "monthly_cost": 500.0,
                "dr_additional_cost": 500.0,
                "multiplier": 1.0,
                "confidence": "high",
                "source": "usage_type_group:Storage",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert len(result) == 1
        assert result[0]["monthly_cost"] == pytest.approx(1500.0)

    def test_sums_dr_additional_cost_across_groups(self):
        """dr_additional_cost is summed across UTG rows."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 0.0,
                "multiplier": 0.0,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Storage",
                "monthly_cost": 500.0,
                "dr_additional_cost": 500.0,
                "multiplier": 1.0,
                "confidence": "high",
                "source": "usage_type_group:Storage",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert result[0]["dr_additional_cost"] == pytest.approx(500.0)

    def test_effective_multiplier_computed_correctly(self):
        """effective multiplier = sum(dr_cost) / sum(monthly_cost)."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 800.0,
                "dr_additional_cost": 400.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Storage",
                "monthly_cost": 200.0,
                "dr_additional_cost": 200.0,
                "multiplier": 1.0,
                "confidence": "high",
                "source": "usage_type_group:Storage",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        # effective_multiplier = (400 + 200) / (800 + 200) = 600 / 1000 = 0.6
        assert result[0]["effective_multiplier"] == pytest.approx(0.6)

    def test_effective_multiplier_zero_division_guard(self):
        """Guard against division by zero when total monthly_cost is zero."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 0.0,
                "dr_additional_cost": 0.0,
                "multiplier": 0.0,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert result[0]["effective_multiplier"] == 0.0

    def test_lowest_confidence_selected(self):
        """lowest confidence among constituent rows is used."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 500.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Storage",
                "monthly_cost": 500.0,
                "dr_additional_cost": 500.0,
                "multiplier": 1.0,
                "confidence": "medium",
                "source": "known",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert result[0]["confidence"] == "medium"

    def test_lowest_confidence_ordering(self):
        """Verify confidence ordering: high > medium-high > medium > low."""
        utg_rows = [
            {
                "service": "ServiceA",
                "usage_type_group": "G1",
                "monthly_cost": 100.0,
                "dr_additional_cost": 50.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:G1",
            },
            {
                "service": "ServiceA",
                "usage_type_group": "G2",
                "monthly_cost": 100.0,
                "dr_additional_cost": 50.0,
                "multiplier": 0.5,
                "confidence": "medium-high",
                "source": "usage_type_group:G2",
            },
            {
                "service": "ServiceA",
                "usage_type_group": "G3",
                "monthly_cost": 100.0,
                "dr_additional_cost": 50.0,
                "multiplier": 0.5,
                "confidence": "low",
                "source": "known",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert result[0]["confidence"] == "low"

    def test_merges_with_unsupported_rows(self):
        """merge UTG-resolved rows with unsupported-service rows."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 500.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
        ]
        unsupported_rows = [
            {
                "service": "Amazon EC2",
                "monthly_cost": 2000.0,
                "dr_additional_cost": 1000.0,
                "multiplier": 0.5,
                "confidence": "medium-high",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows, unsupported_rows)
        assert len(result) == 2
        services = {r["service"] for r in result}
        assert services == {"Amazon Relational Database Service", "Amazon EC2"}

    def test_result_sorted_by_monthly_cost_descending(self):
        """Unified list should be sorted by monthly_cost descending."""
        utg_rows = [
            {
                "service": "Amazon OpenSearch Service",
                "usage_type_group": "Instance Hours",
                "monthly_cost": 300.0,
                "dr_additional_cost": 150.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Instance Hours",
            },
        ]
        unsupported_rows = [
            {
                "service": "Amazon EC2",
                "monthly_cost": 5000.0,
                "dr_additional_cost": 2500.0,
                "multiplier": 0.5,
                "confidence": "medium-high",
            },
            {
                "service": "AWS Lambda",
                "monthly_cost": 100.0,
                "dr_additional_cost": 50.0,
                "multiplier": 0.5,
                "confidence": "medium",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows, unsupported_rows)
        costs = [r["monthly_cost"] for r in result]
        assert costs == sorted(costs, reverse=True)

    def test_confidence_source_included(self):
        """Aggregated rows should include confidence_source field."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 500.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert result[0]["confidence_source"] == (
            "usage-type-group split for Amazon Relational Database Service"
        )

    def test_multiple_services_aggregated_separately(self):
        """Each service gets its own aggregated row."""
        utg_rows = [
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Running Hours",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 500.0,
                "multiplier": 0.5,
                "confidence": "high",
                "source": "usage_type_group:Running Hours",
            },
            {
                "service": "Amazon Relational Database Service",
                "usage_type_group": "Storage",
                "monthly_cost": 200.0,
                "dr_additional_cost": 200.0,
                "multiplier": 1.0,
                "confidence": "high",
                "source": "usage_type_group:Storage",
            },
            {
                "service": "Amazon Redshift",
                "usage_type_group": "Node Hours",
                "monthly_cost": 800.0,
                "dr_additional_cost": 0.0,
                "multiplier": 0.0,
                "confidence": "high",
                "source": "usage_type_group:Node Hours",
            },
            {
                "service": "Amazon Redshift",
                "usage_type_group": "Storage",
                "monthly_cost": 100.0,
                "dr_additional_cost": 10.0,
                "multiplier": 0.1,
                "confidence": "high",
                "source": "usage_type_group:Storage",
            },
        ]

        result = _reaggregate_utg_rows(utg_rows)
        assert len(result) == 2

        # Sorted by monthly_cost descending
        rds_row = next(r for r in result if r["service"] == "Amazon Relational Database Service")
        redshift_row = next(r for r in result if r["service"] == "Amazon Redshift")

        assert rds_row["monthly_cost"] == pytest.approx(1200.0)
        assert rds_row["dr_additional_cost"] == pytest.approx(700.0)

        assert redshift_row["monthly_cost"] == pytest.approx(900.0)
        assert redshift_row["dr_additional_cost"] == pytest.approx(10.0)
        assert redshift_row["effective_multiplier"] == pytest.approx(10.0 / 900.0)

    def test_empty_utg_rows(self):
        """Empty UTG rows with no unsupported rows returns empty list."""
        result = _reaggregate_utg_rows([])
        assert result == []

    def test_empty_utg_rows_with_unsupported(self):
        """Empty UTG rows with unsupported rows returns just unsupported rows."""
        unsupported = [
            {
                "service": "Amazon EC2",
                "monthly_cost": 1000.0,
                "dr_additional_cost": 500.0,
                "multiplier": 0.5,
                "confidence": "medium-high",
            },
        ]
        result = _reaggregate_utg_rows([], unsupported)
        assert len(result) == 1
        assert result[0]["service"] == "Amazon EC2"
