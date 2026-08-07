# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared fake Cost Explorer client for usage-type-group tests.

The fake mirrors the REAL GetCostAndUsage contract, including the
constraint that ``USAGE_TYPE_GROUP`` is only valid as a ``Filter``
dimension: a request that puts it in ``GroupBy`` raises, exactly like
the live API (``ValidationException: Group Definition dimension is
invalid``). The production bug this guards against was a fetcher that
grouped by ``USAGE_TYPE_GROUP`` and silently swallowed the resulting
exception, so the usage-type-group tier never fired against real AWS.

Request routing:

* ``GroupBy=[SERVICE]``: discovery query -> per-service ``Groups`` for
  each month in ``portfolio_by_month``.
* ``Filter`` containing a ``USAGE_TYPE_GROUP`` dimension (and no
  ``GroupBy``): per-group detail query -> per-month ``Total`` blocks
  from ``group_amounts``.
* ``GroupBy`` containing ``USAGE_TYPE_GROUP``: raises (live-API parity).
"""

from __future__ import annotations


class FakeValidationException(Exception):
    """Stands in for botocore's ClientError on an invalid GroupBy."""


def extract_filter_values(filter_expr: dict | None, key: str) -> list[str]:
    """Return the ``Values`` list for dimension ``key`` in a CE Filter."""
    if not filter_expr:
        return []
    blocks = filter_expr.get("And", [filter_expr])
    for block in blocks:
        dims = block.get("Dimensions", {})
        if dims.get("Key") == key:
            return list(dims.get("Values") or [])
    return []


class FakeUTGCEClient:
    """Configurable fake CE client for the usage-type-group fetch path.

    Parameters
    ----------
    portfolio_by_month:
        List of months, each a list of ``(service_display_name, amount)``
        tuples, returned by the discovery (``GroupBy=[SERVICE]``) query.
    group_amounts:
        ``{(service, usage_type_group): [amount_per_month, ...]}`` for
        the per-group filtered detail queries. Groups not present return
        zero-amount months.
    group_query_behavior:
        ``"ok"`` (default) serves ``group_amounts``; ``"empty"`` returns
        no ``ResultsByTime`` periods for every detail query; ``"raise"``
        raises on every detail query.
    paginate_detail:
        When true, detail queries return one month per page chained via
        ``NextPageToken`` so pagination handling is exercised.
    """

    def __init__(
        self,
        *,
        portfolio_by_month: list[list[tuple[str, float]]],
        group_amounts: dict[tuple[str, str], list[float]] | None = None,
        group_query_behavior: str = "ok",
        paginate_detail: bool = False,
    ) -> None:
        self.portfolio_by_month = portfolio_by_month
        self.group_amounts = group_amounts or {}
        self.group_query_behavior = group_query_behavior
        self.paginate_detail = paginate_detail
        self.calls: list[dict] = []

    # -- response builders --------------------------------------------------

    def _month_period(self, index: int) -> dict:
        start = f"2025-{index + 1:02d}-01"
        end = f"2025-{index + 2:02d}-01"
        return {"Start": start, "End": end}

    def _discovery_response(self, kwargs: dict) -> dict:
        results = []
        for index, month in enumerate(self.portfolio_by_month):
            results.append(
                {
                    "TimePeriod": self._month_period(index),
                    "Groups": [
                        {
                            "Keys": [name],
                            "Metrics": {
                                "UnblendedCost": {
                                    "Amount": str(amount),
                                    "Unit": "USD",
                                }
                            },
                        }
                        for name, amount in month
                    ],
                }
            )
        return {"ResultsByTime": results}

    def _detail_response(self, kwargs: dict) -> dict:
        if self.group_query_behavior == "raise":
            raise RuntimeError("ThrottlingException: Rate exceeded")
        if self.group_query_behavior == "empty":
            return {"ResultsByTime": []}

        filter_expr = kwargs.get("Filter")
        services = extract_filter_values(filter_expr, "SERVICE")
        groups = extract_filter_values(filter_expr, "USAGE_TYPE_GROUP")
        assert len(services) == 1, f"detail query must filter one SERVICE: {filter_expr}"
        assert len(groups) == 1, (
            f"detail query must filter one USAGE_TYPE_GROUP: {filter_expr}"
        )
        amounts = self.group_amounts.get(
            (services[0], groups[0]),
            [0.0] * len(self.portfolio_by_month),
        )

        periods = [
            {
                "TimePeriod": self._month_period(index),
                "Total": {
                    "UnblendedCost": {"Amount": str(amount), "Unit": "USD"}
                },
                "Groups": [],
            }
            for index, amount in enumerate(amounts)
        ]

        if not self.paginate_detail or len(periods) <= 1:
            return {"ResultsByTime": periods}

        # One month per page, chained by NextPageToken.
        page_index = 0
        token = kwargs.get("NextPageToken")
        if token is not None:
            page_index = int(token.rsplit("-", 1)[-1])
        response: dict = {"ResultsByTime": [periods[page_index]]}
        if page_index + 1 < len(periods):
            response["NextPageToken"] = f"detail-page-{page_index + 1}"
        return response

    # -- the one API surface -------------------------------------------------

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)

        group_by = kwargs.get("GroupBy", [])
        if any(g.get("Key") == "USAGE_TYPE_GROUP" for g in group_by):
            # Live-API parity: USAGE_TYPE_GROUP is not a valid GroupBy
            # dimension for GetCostAndUsage.
            raise FakeValidationException(
                "ValidationException: Group Definition dimension is invalid."
            )

        filter_expr = kwargs.get("Filter")
        if extract_filter_values(filter_expr, "USAGE_TYPE_GROUP"):
            return self._detail_response(kwargs)

        return self._discovery_response(kwargs)

    # -- assertion helpers ----------------------------------------------------

    @property
    def detail_calls(self) -> list[dict]:
        return [
            c
            for c in self.calls
            if extract_filter_values(c.get("Filter"), "USAGE_TYPE_GROUP")
        ]

    @property
    def discovery_calls(self) -> list[dict]:
        return [
            c
            for c in self.calls
            if any(
                g.get("Key") == "SERVICE" for g in c.get("GroupBy", [])
            )
        ]


def extract_tag_values(filter_expr: dict | None, tag_key: str) -> list[str]:
    """Return the ``Values`` list for the ``Tags`` block with ``tag_key``."""
    if not filter_expr:
        return []
    blocks = filter_expr.get("And", [filter_expr])
    for block in blocks:
        tags = block.get("Tags", {})
        if tags.get("Key") == tag_key:
            return list(tags.get("Values") or [])
    return []
