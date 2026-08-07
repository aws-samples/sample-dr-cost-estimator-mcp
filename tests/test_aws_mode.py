# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for the Cost Explorer tools.

Uses a hand-rolled fake in place of ``boto3.client('ce')`` to keep the
tests offline and free of the ``moto`` dependency. The fake mirrors the
shape Cost Explorer's ``GetCostAndUsage`` returns for a
``Granularity=MONTHLY`` + ``GroupBy=SERVICE`` query.

Covers ``analyze_dr_strategy`` (single-strategy deep dive) and
``compare_dr_strategies`` (lightweight comparison across all four
strategies).
"""

import asyncio

import pytest

from dr_cost_estimator_mcp.core.multipliers import STRATEGIES
from dr_cost_estimator_mcp.tools import DRCostEstimatorTools


class _FakeCEClient:
    """Minimal stand-in for a boto3 Cost Explorer client.

    Returns a fixed two-month response with three services on the first
    call, then a second page with one more service so pagination is
    exercised. Also captures the request kwargs so tests can assert on
    Filter / TimePeriod wiring.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        if "NextPageToken" not in kwargs:
            return {
                "ResultsByTime": [
                    {
                        "TimePeriod": {"Start": "2025-01-01", "End": "2025-02-01"},
                        "Groups": [
                            {
                                "Keys": ["Amazon Simple Storage Service"],
                                "Metrics": {"UnblendedCost": {"Amount": "100.00", "Unit": "USD"}},
                            },
                            {
                                "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                                "Metrics": {"UnblendedCost": {"Amount": "200.00", "Unit": "USD"}},
                            },
                            # Skip-list service: must be filtered out.
                            {
                                "Keys": ["Tax"],
                                "Metrics": {"UnblendedCost": {"Amount": "50.00", "Unit": "USD"}},
                            },
                        ],
                    },
                    {
                        "TimePeriod": {"Start": "2025-02-01", "End": "2025-03-01"},
                        "Groups": [
                            {
                                "Keys": ["Amazon Simple Storage Service"],
                                "Metrics": {"UnblendedCost": {"Amount": "120.00", "Unit": "USD"}},
                            },
                            {
                                "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                                "Metrics": {"UnblendedCost": {"Amount": "240.00", "Unit": "USD"}},
                            },
                        ],
                    },
                ],
                "NextPageToken": "page2",
            }
        # Second page: one additional service, no further pages.
        return {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2025-01-01", "End": "2025-02-01"},
                    "Groups": [
                        {
                            "Keys": ["AmazonCloudWatch"],
                            "Metrics": {"UnblendedCost": {"Amount": "10.00", "Unit": "USD"}},
                        },
                    ],
                },
                {
                    "TimePeriod": {"Start": "2025-02-01", "End": "2025-03-01"},
                    "Groups": [
                        {
                            "Keys": ["AmazonCloudWatch"],
                            "Metrics": {"UnblendedCost": {"Amount": "14.00", "Unit": "USD"}},
                        },
                    ],
                },
            ],
        }


def _run(coro):
    return asyncio.run(coro)


def _assert_next_steps_populated(response) -> None:
    """Every error response must carry 1-3 plain-text remediation steps."""
    assert response.error is not None
    assert isinstance(response.error.next_steps, list)
    assert 1 <= len(response.error.next_steps) <= 3
    for step in response.error.next_steps:
        assert isinstance(step, str) and step.strip()


def test_analyze_dr_strategy_happy_path() -> None:
    fake = _FakeCEClient()
    tools = DRCostEstimatorTools()

    response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=fake,
        )
    )

    assert response.success is True
    assert response.error is None
    assert response.strategy == "warm_standby"

    services = {row.service for row in response.estimates}
    assert "Amazon Simple Storage Service" in services
    assert "Amazon Elastic Compute Cloud - Compute" in services
    assert "AmazonCloudWatch" in services
    # Skip-list rows removed.
    assert "Tax" not in services

    # Per-row sums match the reported totals within rounding tolerance.
    total_current = sum(row.current_cost for row in response.estimates)
    assert total_current > 0
    assert response.totals is not None
    n = len(response.estimates)
    row_sum_tol = 0.005 * n + 0.01
    assert abs(response.totals.total_current_cost - total_current) <= row_sum_tol

    # Per-account section is always present. For Cost Explorer inputs the
    # synthetic entry uses account_id="unknown" /
    # account_name="Cost Explorer Export".
    assert len(response.per_account) == 1
    only = response.per_account[0]
    assert only.account_id == "unknown"
    assert only.account_name == "Cost Explorer Export"


def test_compare_dr_strategies_happy_path() -> None:
    fake = _FakeCEClient()
    tools = DRCostEstimatorTools()

    response = _run(
        tools.compare_dr_strategies(
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=fake,
        )
    )

    assert response.success is True
    assert response.error is None
    assert len(response.top_services) > 0
    assert response.per_strategy_totals
    # Each service has all four strategies populated.
    for svc in response.top_services:
        assert set(svc.per_strategy.keys()) == set(STRATEGIES)

    # Per-account totals are always present.
    assert len(response.per_account_totals) == 1
    assert response.per_account_totals[0].account_id == "unknown"


def test_linked_account_ids_filter_wired_through() -> None:
    fake = _FakeCEClient()
    tools = DRCostEstimatorTools()

    _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=["111122223333", "444455556666"],
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=fake,
        )
    )

    # The first call to the fake should carry a Filter block with both accounts.
    assert len(fake.calls) >= 1
    first_call = fake.calls[0]
    assert "Filter" in first_call
    assert first_call["Filter"] == {
        "Dimensions": {
            "Key": "LINKED_ACCOUNT",
            "Values": ["111122223333", "444455556666"],
        }
    }


@pytest.mark.parametrize(
    "bad_ids,expected_substring",
    [
        ([""], "non-empty strings"),
        (["abc"], "12-digit"),
        (["12345"], "12-digit"),
    ],
)
def test_linked_account_ids_validation(bad_ids, expected_substring) -> None:
    tools = DRCostEstimatorTools()
    response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=bad_ids,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=_FakeCEClient(),
        )
    )
    assert response.success is False
    assert response.error is not None
    assert response.error.code == "INVALID_INPUT"
    assert expected_substring in response.error.message
    _assert_next_steps_populated(response)


def test_months_back_out_of_range() -> None:
    tools = DRCostEstimatorTools()
    response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=0,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=_FakeCEClient(),
        )
    )
    assert response.success is False
    assert response.error is not None
    assert response.error.code == "INVALID_INPUT"
    _assert_next_steps_populated(response)


def test_boto3_exception_is_mapped_to_aws_error() -> None:
    class _BoomClient:
        def get_cost_and_usage(self, **kwargs):
            raise RuntimeError("AccessDenied: ce:GetCostAndUsage")

    tools = DRCostEstimatorTools()
    response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=_BoomClient(),
        )
    )

    assert response.success is False
    assert response.error is not None
    assert response.error.code == "AWS_ERROR"
    assert "AccessDenied" in response.error.message
    _assert_next_steps_populated(response)


def test_empty_ce_response_reported_as_aws_error() -> None:
    class _EmptyClient:
        def get_cost_and_usage(self, **kwargs):
            return {"ResultsByTime": []}

    tools = DRCostEstimatorTools()
    response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=_EmptyClient(),
        )
    )

    assert response.success is False
    assert response.error is not None
    assert response.error.code == "AWS_ERROR"
    _assert_next_steps_populated(response)


def test_aws_mode_only_calls_get_cost_and_usage() -> None:
    """AWS-mode tools must call only ``get_cost_and_usage`` on the CE client.

    The ``compare_dr_strategies`` and ``analyze_dr_strategy`` tools
    require only the ``ce:GetCostAndUsage`` permission and must not
    introduce any additional AWS API calls or IAM permission
    requirements.

    The test wraps a recording stub around ``_FakeCEClient`` and asserts
    that the only method name forwarded to the underlying boto3 surface
    is ``get_cost_and_usage`` for both AWS-mode tools.
    """

    class _RecordingCEClient:
        """Records the name of every method invoked on the client.

        Forwards calls to an inner :class:`_FakeCEClient` so the tools
        receive a realistic Cost Explorer response, then asserts on the
        recorded list to detect any new boto3 surface that would expand
        the IAM scope beyond ``ce:GetCostAndUsage``.
        """

        def __init__(self) -> None:
            self.method_calls: list[str] = []
            self._inner = _FakeCEClient()

        def __getattr__(self, name: str):
            # Dunder attributes are reserved for Python internals and are
            # never used as a Cost Explorer API surface; let them resolve
            # via the normal attribute path so __getattr__ is not invoked
            # for, e.g., copy.deepcopy probes.
            if name.startswith("_"):
                raise AttributeError(name)

            inner_attr = getattr(self._inner, name, None)

            def _recorder(*args, **kwargs):
                self.method_calls.append(name)
                if inner_attr is None:
                    raise AssertionError(
                        f"AWS-mode tools called {name!r} on the CE "
                        "client; only get_cost_and_usage is permitted."
                    )
                return inner_attr(*args, **kwargs)

            return _recorder

    tools = DRCostEstimatorTools()

    # analyze_dr_strategy
    analyze_recorder = _RecordingCEClient()
    analyze_response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=analyze_recorder,
        )
    )
    assert analyze_response.success is True
    assert analyze_recorder.method_calls, (
        "analyze_dr_strategy did not invoke any method on the "
        "CE client; the recording stub never observed a call."
    )
    assert set(analyze_recorder.method_calls) == {"get_cost_and_usage"}, (
        "analyze_dr_strategy must call only get_cost_and_usage; "
        f"observed: {analyze_recorder.method_calls}"
    )

    # compare_dr_strategies
    compare_recorder = _RecordingCEClient()
    compare_response = _run(
        tools.compare_dr_strategies(
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=compare_recorder,
        )
    )
    assert compare_response.success is True
    assert compare_recorder.method_calls, (
        "compare_dr_strategies did not invoke any method on the "
        "CE client; the recording stub never observed a call."
    )
    assert set(compare_recorder.method_calls) == {"get_cost_and_usage"}, (
        "compare_dr_strategies must call only "
        f"get_cost_and_usage; observed: {compare_recorder.method_calls}"
    )


def test_fetch_usage_type_group_contract() -> None:
    """The fetch path has a stable request/response shape.

      * the return value is the usage-type-group dict with ``utg_rows``,
        ``aggregated_rows``, ``warnings``, and ``utg_resolved_services``;
      * portfolios without UTG-supported services yield only aggregated
        rows carrying ``service`` and ``monthly_cost``;
      * the discovery request's ``GroupBy`` is the single-element
        SERVICE list;
      * no ``Filter`` is added when none of the optional filters are
        supplied;
      * skip-list services are still removed.
    """
    from dr_cost_estimator_mcp.core.aws_costs import fetch_services_from_aws

    fake = _FakeCEClient()
    result = fetch_services_from_aws(months_back=2, client=fake)

    assert set(result.keys()) == {
        "utg_rows",
        "aggregated_rows",
        "warnings",
        "utg_resolved_services",
    }

    # No UTG-supported services in the fake portfolio: everything stays
    # on the aggregated path with just ``service`` and ``monthly_cost``.
    assert result["utg_rows"] == []
    assert result["utg_resolved_services"] == set()
    rows = result["aggregated_rows"]
    assert rows  # non-empty
    for row in rows:
        assert set(row.keys()) == {"service", "monthly_cost"}
        assert row["monthly_cost"] > 0

    services = {row["service"] for row in rows}
    assert "Amazon Simple Storage Service" in services
    assert "Amazon Elastic Compute Cloud - Compute" in services
    assert "AmazonCloudWatch" in services
    assert "Tax" not in services  # skip-list filter still applies.

    # Request shape: single-key GroupBy, no Filter when no filters
    # were supplied. The fake records both the first-page and the
    # second-page request; assert the same shape on each.
    assert len(fake.calls) >= 1
    for request_kwargs in fake.calls:
        assert request_kwargs["GroupBy"] == [
            {"Type": "DIMENSION", "Key": "SERVICE"}
        ]
        assert "Filter" not in request_kwargs


def test_fetch_empty_ce_result_returns_empty_rows() -> None:
    """Empty CE responses yield empty row lists at the fetcher boundary.

    The fetcher's contract on an empty Cost Explorer response is a
    result dict with empty ``utg_rows`` and ``aggregated_rows``; the
    higher-layer ``ToolError("AWS_ERROR", ...)`` with the documented
    three-cause ``next_steps`` is raised by the orchestrator (see
    ``_load_from_aws`` in ``tools.py``). This test pins down the
    lower-layer empty-rows contract the tool layer relies on.
    """
    from dr_cost_estimator_mcp.core.aws_costs import fetch_services_from_aws

    class _EmptyFakeCEClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def get_cost_and_usage(self, **kwargs):
            self.calls.append(kwargs)
            return {"ResultsByTime": []}

    empty_fake = _EmptyFakeCEClient()
    result = fetch_services_from_aws(months_back=2, client=empty_fake)
    assert result["utg_rows"] == []
    assert result["aggregated_rows"] == []
    assert len(empty_fake.calls) == 1


def test_compare_summary_does_not_claim_an_account_count() -> None:
    """Regression: the compare summary must not say "Across 1 account".

    Cost Explorer aggregates carry no per-account breakdown, so the
    per-account section holds one SYNTHETIC entry covering the whole
    query scope - which spans every linked account visible to the
    profile (an org payer can cover dozens). Counting that entry as
    "1 account" is false in exactly the payer case that matters, so the
    summary must describe the scope instead of claiming a count.
    """
    fake = _FakeCEClient()
    tools = DRCostEstimatorTools()
    response = _run(
        tools.compare_dr_strategies(
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=fake,
        )
    )
    assert response.success is True
    assert "Across 1 account" not in response.summary
    assert response.summary.startswith(
        "Across all spend visible to the queried Cost Explorer scope"
    )
