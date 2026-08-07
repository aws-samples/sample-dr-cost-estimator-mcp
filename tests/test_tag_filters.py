# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for tag-based workload scoping (``tag_filters``).

Tag filters turn the account/portfolio estimate into a per-workload one
by restricting every Cost Explorer query to spend carrying the given
cost allocation tags. The critical invariants:

* EVERY query carries the tag scope - the discovery query AND each
  per-usage-type-group detail query - or the split would mix workload
  and non-workload spend.
* Responses carry a warning making the untagged-spend blind spot
  explicit.
* Validation rejects malformed shapes with INVALID_INPUT.
"""

from __future__ import annotations

import asyncio

import pytest

from dr_cost_estimator_mcp.tools import DRCostEstimatorTools

from tests.utg_fakes import FakeUTGCEClient, extract_tag_values

EC2 = "Amazon Elastic Compute Cloud - Compute"
RDS = "Amazon Relational Database Service"

TAGS = {"Project": ["checkout", "payments"], "Environment": ["prod"]}


def _run(coro):
    return asyncio.run(coro)


def _fake() -> FakeUTGCEClient:
    return FakeUTGCEClient(
        portfolio_by_month=[[(EC2, 1000.0), (RDS, 400.0)]],
        group_amounts={
            (RDS, "RDS: Running Hours"): [300.0],
            (RDS, "RDS: Storage"): [100.0],
        },
    )


def _analyze(client, tag_filters, strategy: str = "warm_standby"):
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
            tag_filters=tag_filters,
            client=client,
        )
    )


class TestTagScopeWiring:
    def test_every_ce_query_carries_the_tag_filters(self) -> None:
        """Discovery AND per-group detail queries must all be tag-scoped."""
        fake = _fake()
        response = _analyze(fake, TAGS)
        assert response.success, response.error

        assert fake.calls, "no CE calls recorded"
        for call in fake.calls:
            filter_expr = call.get("Filter")
            assert extract_tag_values(filter_expr, "Project") == [
                "checkout",
                "payments",
            ], f"call missing Project tag scope: {filter_expr}"
            assert extract_tag_values(filter_expr, "Environment") == ["prod"], (
                f"call missing Environment tag scope: {filter_expr}"
            )
        # Both query kinds were actually exercised.
        assert fake.discovery_calls
        assert fake.detail_calls

    def test_response_carries_untagged_spend_warning(self) -> None:
        response = _analyze(_fake(), TAGS)
        assert response.success, response.error
        tag_warnings = [
            w for w in response.warnings if "cost allocation tags" in w
        ]
        assert tag_warnings, response.warnings
        warning = tag_warnings[0]
        assert "Project=checkout|payments" in warning
        assert "Environment=prod" in warning
        assert "untagged" in warning.lower()

    def test_no_tag_filters_means_no_tags_block_and_no_warning(self) -> None:
        fake = _fake()
        response = _analyze(fake, None)
        assert response.success, response.error
        for call in fake.calls:
            assert extract_tag_values(call.get("Filter"), "Project") == []
        assert not any(
            "cost allocation tags" in w for w in response.warnings
        )

    def test_compare_tool_scopes_and_warns_identically(self) -> None:
        fake = _fake()
        tools = DRCostEstimatorTools()
        response = _run(
            tools.compare_dr_strategies(
                months_back=1,
                linked_account_ids=None,
                profile=None,
                region=None,
                top_n=25,
                include_all_services=True,
                tag_filters={"Project": ["checkout"]},
                client=fake,
            )
        )
        assert response.success, response.error
        for call in fake.calls:
            assert extract_tag_values(call.get("Filter"), "Project") == [
                "checkout"
            ]
        assert any("cost allocation tags" in w for w in response.warnings)

    def test_tags_compose_with_linked_accounts(self) -> None:
        fake = _fake()
        response = _run(
            DRCostEstimatorTools().analyze_dr_strategy(
                strategy="pilot_light",
                months_back=1,
                linked_account_ids=["111122223333"],
                profile=None,
                region=None,
                top_n=25,
                include_all_services=True,
                tag_filters={"Project": ["checkout"]},
                client=fake,
            )
        )
        assert response.success, response.error
        first = fake.calls[0]["Filter"]
        # Both the account and tag blocks are present, ANDed.
        assert "And" in first
        assert extract_tag_values(first, "Project") == ["checkout"]
        blocks = first["And"]
        assert any(
            b.get("Dimensions", {}).get("Key") == "LINKED_ACCOUNT"
            for b in blocks
        )


class TestTagFilterValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            {},                                  # empty dict
            {"": ["x"]},                         # empty key
            {"Project": []},                     # empty values
            {"Project": [""]},                   # empty value string
            {"Project": "checkout"},             # values not a list
            {"Project": [1, 2]},                 # non-string values
            {f"k{i}": ["v"] for i in range(11)},  # too many keys
            {"Project": [f"v{i}" for i in range(21)]},  # too many values
        ],
    )
    def test_malformed_tag_filters_rejected(self, bad) -> None:
        response = _analyze(_fake(), bad)
        assert response.success is False
        assert response.error is not None
        assert response.error.code == "INVALID_INPUT"
        assert "tag_filters" in response.error.message

    def test_zero_result_error_mentions_tag_activation(self) -> None:
        empty = FakeUTGCEClient(portfolio_by_month=[[]])
        response = _analyze(empty, {"Project": ["nonexistent"]})
        assert response.success is False
        assert response.error.code == "AWS_ERROR"
        assert "cost allocation tag" in response.error.message
        assert "Project" in response.error.message
