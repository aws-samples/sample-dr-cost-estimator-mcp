# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AWS Cost Explorer fetcher for the DR cost estimator.

Fetches service-level monthly spend via the Cost Explorer
``GetCostAndUsage`` API and returns ``{"service", "monthly_cost"}``
rows for the downstream estimate / compare pipeline.

Cost Explorer is a global service and is always accessed via the
``us-east-1`` endpoint regardless of where the caller's workload runs.
The ``region`` parameter on the MCP tools is the AWS region the boto3
*session* should use for credential resolution, not the CE endpoint.

Usage-type-group splitting
==========================

:func:`fetch_services_from_aws` first aggregates the response to a
service-level monthly average - one row per service, with skip-list
services (tax, refunds, support) removed - then splits supported
services (RDS, OpenSearch, ELB) by their Cost Explorer usage type
groups so, for example, compute and storage spend can resolve against
separate multipliers.

The Cost Explorer API does NOT support ``USAGE_TYPE_GROUP`` as a
``GroupBy`` dimension (only as a ``Filter``), so the split is done by
issuing one filtered ``GetCostAndUsage`` query per configured group and
computing a remainder row (:data:`UTG_REMAINDER_GROUP`) for any spend
inside the service that no configured group captured. The remainder row
resolves via the blended service-level multiplier downstream.
Unsupported services keep the aggregated service-level shape.
"""

from collections import defaultdict
from datetime import date
from typing import Optional

import boto3

from .multipliers import (
    SUPPORTED_UTG_SERVICES,
    UTG_REMAINDER_GROUP,
    should_skip_service,
    utg_groups_for_service,
)

# Cost Explorer is a global service; the service endpoint lives in
# us-east-1 regardless of where the caller's workload or the profile's
# default region is configured.
CE_ENDPOINT_REGION = "us-east-1"

# Cost metric requested from Cost Explorer. UnblendedCost matches what
# the Cost Explorer console shows by default.
CE_METRIC = "UnblendedCost"


def _first_of_month(year: int, month: int) -> str:
    """Return 'YYYY-MM-01' formatted for the CE Start/End fields."""
    return f"{year:04d}-{month:02d}-01"


def _months_back_window(months_back: int, today: Optional[date] = None) -> tuple[str, str]:
    """Return ``(start_date, end_date)`` strings for the last ``months_back`` complete months.

    CE uses half-open intervals: ``Start`` is inclusive, ``End`` is
    exclusive. To get the last N complete months up to but not including
    the current month, we anchor ``End`` at the first day of the current
    month and roll ``Start`` back N months.
    """
    today = today or date.today()
    end_year, end_month = today.year, today.month
    start_month = end_month - months_back
    start_year = end_year
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    return _first_of_month(start_year, start_month), _first_of_month(end_year, end_month)


def _build_filter(
    linked_account_ids: Optional[list[str]],
    service_filter: Optional[list[str]] = None,
    usage_type_group_filter: Optional[list[str]] = None,
    tag_filters: Optional[dict[str, list[str]]] = None,
) -> Optional[dict]:
    """Build a CE ``Filter`` dict combining the supplied dimension filters.

    Composes one ``Dimensions`` block per supplied filter and ANDs them
    together. When exactly one filter is supplied, the bare
    ``Dimensions`` block is returned (no ``And`` wrapper) to preserve
    the existing single-filter request shape used by callers that only
    pass ``linked_account_ids``. When multiple filters are supplied,
    they are wrapped in a single ``And`` list. Returns ``None`` when no
    filter is requested so the caller can omit the ``Filter`` request
    field entirely.

    :param linked_account_ids: Optional list of 12-digit AWS account
        IDs to restrict the query to via the ``LINKED_ACCOUNT``
        dimension.
    :param service_filter: Optional list of Cost Explorer ``SERVICE``
        dimension values (e.g. ``["Amazon Elastic Compute Cloud -
        Compute"]``) to restrict the query to.
    :param usage_type_group_filter: Optional list of Cost Explorer
        ``USAGE_TYPE_GROUP`` dimension values (e.g.
        ``["RDS: Running Hours"]``) to restrict the query to.
        ``USAGE_TYPE_GROUP`` is only valid as a filter, never as a
        ``GroupBy`` dimension.
    :param tag_filters: Optional mapping of cost allocation tag key to
        accepted values (e.g. ``{"Project": ["checkout", "payments"],
        "Environment": ["prod"]}``). One ``Tags`` block is emitted per
        key and ANDed with the other filters; values within a key are
        ORed by Cost Explorer. Tag keys must be ACTIVATED as cost
        allocation tags in the billing console to match anything.
    """
    sub_filters: list[dict] = []
    if linked_account_ids:
        sub_filters.append(
            {
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": list(linked_account_ids),
                }
            }
        )
    if service_filter:
        sub_filters.append(
            {
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": list(service_filter),
                }
            }
        )
    if usage_type_group_filter:
        sub_filters.append(
            {
                "Dimensions": {
                    "Key": "USAGE_TYPE_GROUP",
                    "Values": list(usage_type_group_filter),
                }
            }
        )
    if tag_filters:
        # Sort keys so the request shape is deterministic for a given
        # input (stable for tests and request logging).
        for tag_key in sorted(tag_filters):
            sub_filters.append(
                {
                    "Tags": {
                        "Key": tag_key,
                        "Values": list(tag_filters[tag_key]),
                    }
                }
            )

    if not sub_filters:
        return None
    if len(sub_filters) == 1:
        return sub_filters[0]
    return {"And": sub_filters}


def fetch_services_from_aws(
    months_back: int,
    linked_account_ids: Optional[list[str]] = None,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    client: Optional[object] = None,
    tag_filters: Optional[dict[str, list[str]]] = None,
) -> dict:
    """Fetch monthly spend from Cost Explorer, split by usage type group.

    Supported services (RDS, OpenSearch, ELB) are split by their Cost
    Explorer usage type groups via per-group filtered queries (plus a
    remainder row) for granular compute-vs-storage multiplier
    resolution; unsupported services use the aggregated service-level
    path.

    :param months_back: Number of complete months of history to fetch
        (1-24). CE ``MONTHLY`` granularity is used.
    :param linked_account_ids: Optional list of AWS account ids to
        restrict the query to. When omitted, all accounts visible to the
        caller's payer account are included.
    :param profile: Optional AWS profile name. When omitted, the default
        credential chain is used.
    :param region: Optional AWS region for the boto3 session (does not
        change the CE endpoint, which is always us-east-1).
    :param client: Optional pre-built CE client. When supplied, bypasses
        boto3 session construction and credential resolution. Intended
        for tests.
    :param tag_filters: Optional cost-allocation-tag scope (tag key ->
        accepted values) applied to EVERY query this fetch issues -
        discovery and per-group detail alike - so the whole estimate is
        consistently restricted to the tagged workload.

    :returns: A dict: ``{"utg_rows": list[dict],
        "aggregated_rows": list[dict], "warnings": list[str],
        "utg_resolved_services": set[str]}`` (see
        :func:`_fetch_with_usage_type_groups`). Costs are averaged
        across the requested months with skip-list services removed.
    """
    if client is None:
        session_kwargs: dict = {}
        if profile:
            session_kwargs["profile_name"] = profile
        if region:
            session_kwargs["region_name"] = region
        session = boto3.Session(**session_kwargs)
        client = session.client("ce", region_name=CE_ENDPOINT_REGION)

    start, end = _months_back_window(months_back)

    return _fetch_with_usage_type_groups(
        client=client,
        start=start,
        end=end,
        linked_account_ids=linked_account_ids,
        tag_filters=tag_filters,
    )


def _fetch_aggregated(
    *,
    client: object,
    start: str,
    end: str,
    linked_account_ids: Optional[list[str]],
    tag_filters: Optional[dict[str, list[str]]] = None,
) -> list[dict]:
    """Aggregated discovery: one ``GroupBy=[SERVICE]`` query, sum across months.

    Used by :func:`_fetch_with_usage_type_groups` to discover all
    services with non-zero spend before the per-service usage-type-group
    detail queries.
    Returns a sorted list of ``{"service", "monthly_cost"}`` dicts.
    """
    request_kwargs: dict = {
        "TimePeriod": {"Start": start, "End": end},
        "Granularity": "MONTHLY",
        "Metrics": [CE_METRIC],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }
    ce_filter = _build_filter(linked_account_ids, tag_filters=tag_filters)
    if ce_filter is not None:
        request_kwargs["Filter"] = ce_filter

    totals_by_service: dict[str, float] = defaultdict(float)
    month_count = 0
    next_token: Optional[str] = None
    while True:
        if next_token:
            request_kwargs["NextPageToken"] = next_token
        elif "NextPageToken" in request_kwargs:
            del request_kwargs["NextPageToken"]

        response = client.get_cost_and_usage(**request_kwargs)
        for period in response.get("ResultsByTime", []):
            month_count += 1
            for group in period.get("Groups", []):
                keys = group.get("Keys") or []
                amount = _amount_from_group(group)
                service_name = keys[0] if keys else ""
                totals_by_service[service_name] += amount

        next_token = response.get("NextPageToken")
        if not next_token:
            break

    if month_count == 0:
        return []

    rows: list[dict] = []
    for service_name, total in totals_by_service.items():
        if not service_name:
            continue
        if should_skip_service(service_name):
            continue
        monthly_cost = total / month_count
        if monthly_cost <= 0:
            continue
        rows.append({"service": service_name, "monthly_cost": monthly_cost})

    rows.sort(key=lambda r: r["monthly_cost"], reverse=True)
    return rows


def _fetch_with_usage_type_groups(
    *,
    client: object,
    start: str,
    end: str,
    linked_account_ids: Optional[list[str]],
    tag_filters: Optional[dict[str, list[str]]] = None,
) -> dict:
    """Usage-type-group mode: discover services, partition into supported/unsupported.

    For supported services (RDS, OpenSearch, ELB), issues one filtered
    ``GetCostAndUsage`` query per configured usage type group via
    :func:`_fetch_usage_type_groups` (Cost Explorer does not accept
    ``USAGE_TYPE_GROUP`` as a ``GroupBy`` dimension). For unsupported
    services, uses the aggregated SERVICE-level rows from the discovery
    call directly.

    On per-service UTG failure (error or no configured group with
    spend), falls back to the aggregated row for that service and
    appends a warning.

    Returns a dict with:
      - ``utg_rows``: list of per-usage-type-group dicts for supported services
      - ``aggregated_rows``: list of ``{"service", "monthly_cost"}`` dicts for
        unsupported services (and supported services that fell back)
      - ``warnings``: list of warning strings for fallback scenarios
      - ``utg_resolved_services``: set of service display names successfully
        resolved via the usage-type-group path
    """
    # Step 1: Run the aggregated discovery query to get all services with
    # non-zero spend. This reuses the existing _fetch_aggregated logic.
    all_aggregated_rows = _fetch_aggregated(
        client=client,
        start=start,
        end=end,
        linked_account_ids=linked_account_ids,
        tag_filters=tag_filters,
    )

    # Step 2: Partition services into supported (UTG-eligible) and unsupported.
    supported_rows: list[dict] = []
    unsupported_rows: list[dict] = []
    for row in all_aggregated_rows:
        if row["service"] in SUPPORTED_UTG_SERVICES:
            supported_rows.append(row)
        else:
            unsupported_rows.append(row)

    # Step 3: For each supported service, attempt UTG detail query.
    utg_rows: list[dict] = []
    utg_resolved_services: set[str] = set()
    warnings: list[str] = []
    fallback_rows: list[dict] = []

    for row in supported_rows:
        service_name = row["service"]
        detail_rows = _fetch_usage_type_groups(
            client=client,
            start=start,
            end=end,
            service_name=service_name,
            linked_account_ids=linked_account_ids,
            service_monthly_total=row["monthly_cost"],
            tag_filters=tag_filters,
        )

        if detail_rows:
            # Successfully resolved via UTG path.
            utg_rows.extend(detail_rows)
            utg_resolved_services.add(service_name)
        else:
            # Fallback: UTG query returned no data for this service.
            # Use the aggregated row and record a warning.
            fallback_rows.append(row)
            warnings.append(
                f"Usage-type-group query returned no data for "
                f"'{service_name}'; using blended service-level multiplier."
            )

    # Combine unsupported rows with fallback rows for the aggregated output.
    aggregated_rows = unsupported_rows + fallback_rows
    aggregated_rows.sort(key=lambda r: r["monthly_cost"], reverse=True)

    return {
        "utg_rows": utg_rows,
        "aggregated_rows": aggregated_rows,
        "warnings": warnings,
        "utg_resolved_services": utg_resolved_services,
    }


#: Ignore remainders below this monthly-dollar threshold. Filters out
#: floating-point residue from the monthly averaging without hiding any
#: spend a human would notice.
_REMAINDER_EPSILON_USD = 0.01


def _fetch_usage_type_groups(
    *,
    client: object,
    start: str,
    end: str,
    service_name: str,
    linked_account_ids: list[str] | None,
    service_monthly_total: float,
    tag_filters: Optional[dict[str, list[str]]] = None,
) -> list[dict]:
    """Fetch per-usage-type-group spend for one service, return per-group rows.

    Each row: {"service": str, "usage_type_group": str, "monthly_cost": float}
    Returns [] on error or when no configured group has spend (triggers
    fallback to the blended service-level multiplier).

    Cost Explorer does not support ``USAGE_TYPE_GROUP`` as a ``GroupBy``
    dimension, so this issues one ``GetCostAndUsage`` query per group
    configured for the service in ``usage_type_group_multipliers.json``,
    filtered to ``SERVICE == service_name AND USAGE_TYPE_GROUP == <group>``
    (plus ``LINKED_ACCOUNT`` when supplied). Each query's monthly average
    is ``sum(period totals) / month_count`` with pagination consumed to
    completion.

    Spend inside the service that no configured group captured is
    emitted as a synthetic remainder row
    (``usage_type_group = UTG_REMAINDER_GROUP``) computed as
    ``service_monthly_total - sum(matched groups)``, so the service's
    total is preserved exactly and the un-modeled slice resolves via the
    blended service-level multiplier downstream.
    """
    groups = utg_groups_for_service(service_name)
    if not groups:
        return []

    try:
        rows: list[dict] = []
        matched_total = 0.0

        for usage_type_group in groups:
            request_kwargs: dict = {
                "TimePeriod": {"Start": start, "End": end},
                "Granularity": "MONTHLY",
                "Metrics": [CE_METRIC],
            }
            ce_filter = _build_filter(
                linked_account_ids,
                service_filter=[service_name],
                usage_type_group_filter=[usage_type_group],
                tag_filters=tag_filters,
            )
            if ce_filter is not None:
                request_kwargs["Filter"] = ce_filter

            total = 0.0
            month_count = 0
            next_token: Optional[str] = None

            while True:
                if next_token:
                    request_kwargs["NextPageToken"] = next_token
                elif "NextPageToken" in request_kwargs:
                    del request_kwargs["NextPageToken"]

                response = client.get_cost_and_usage(**request_kwargs)

                for period in response.get("ResultsByTime", []):
                    month_count += 1
                    total += _amount_from_total(period)

                next_token = response.get("NextPageToken")
                if not next_token:
                    break

            if month_count == 0:
                continue
            monthly_cost = total / month_count
            if monthly_cost <= 0:
                continue

            rows.append(
                {
                    "service": service_name,
                    "usage_type_group": usage_type_group,
                    "monthly_cost": monthly_cost,
                }
            )
            matched_total += monthly_cost

        if not rows:
            return []

        # Remainder: spend the configured groups did not capture. Resolves
        # via the blended service-level multiplier downstream.
        remainder = service_monthly_total - matched_total
        if remainder > _REMAINDER_EPSILON_USD:
            rows.append(
                {
                    "service": service_name,
                    "usage_type_group": UTG_REMAINDER_GROUP,
                    "monthly_cost": remainder,
                }
            )

        return rows

    except Exception:
        # Graceful fallback: return empty list on any error so the caller
        # can fall back to the blended service-level multiplier.
        return []


def _amount_from_total(period: dict) -> float:
    """Extract the ``UnblendedCost`` amount from a CE ``ResultsByTime`` period.

    Used for un-grouped (filter-only) queries, where the period carries
    a ``Total`` block instead of ``Groups``. Robust to malformed /
    missing ``Amount`` strings; coerces to ``0.0``.
    """
    metrics = period.get("Total", {}).get(CE_METRIC, {})
    amount_raw = metrics.get("Amount", "0")
    try:
        return float(amount_raw)
    except (TypeError, ValueError):
        return 0.0


def _amount_from_group(group: dict) -> float:
    """Extract the ``UnblendedCost`` amount from a CE group dict.

    Robust to malformed / missing ``Amount`` strings; coerces to
    ``0.0`` so a single bad row cannot abort the entire fetch.
    """
    metrics = group.get("Metrics", {}).get(CE_METRIC, {})
    amount_raw = metrics.get("Amount", "0")
    try:
        return float(amount_raw)
    except (TypeError, ValueError):
        return 0.0
