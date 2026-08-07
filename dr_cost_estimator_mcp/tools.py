# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""MCP tool adapter layer for the AWS DR Cost Estimator server.

This module hosts the shared input-handling primitives used by the tool
methods and exposes three MCP tools:

* ``list_dr_strategies`` - enumerate supported DR strategies.
* ``compare_dr_strategies`` - lightweight portfolio-wide
  comparison across all four DR strategies from live AWS Cost Explorer
  data.
* ``analyze_dr_strategy`` - single-strategy deep-dive analysis
  with per-service detail, dollar-weighted confidence, and a
  transparent Top-N rollup, from live AWS Cost Explorer data.

Key internal components:

* :class:`ToolError` - internal exception carrying a structured error
  ``code`` and ``message`` translated by :func:`_error_response` into a
  ``success=False`` envelope with actionable ``next_steps``.
* :func:`_load_from_aws` - validates inputs and wraps
  :func:`~dr_cost_estimator_mcp.core.aws_costs.fetch_services_from_aws`
  so no raw boto3 exception leaks to the MCP client.
* :func:`_build_compare_dr_strategies_response` and
  :func:`_build_analyze_response` - the two response builders that
  produce the top-level Pydantic models in
  :mod:`dr_cost_estimator_mcp.models`.
* :data:`NEXT_STEPS_TABLE` - keyed by error code, producing 1–3
  plain-text remediation steps.
"""

from __future__ import annotations

import logging
from typing import Optional

from .consts import (
    CE_SYNTHETIC_ACCOUNT_ID,
    CE_SYNTHETIC_ACCOUNT_NAME,
    DEFAULT_TOP_N,
)
from .core.confidence import (
    ConfidenceLevel,
    build_confidence_summary,
    source_to_confidence,
)
from .core.errors import ToolError
from .core.estimates import (
    ROLLUP_NAME_RE,
    TopNRolloverResult,
    apply_top_n,
    compute_estimates,
)
from .core.multipliers import (
    DEFAULT_WARM_CAPACITY,
    STRATEGIES,
    STRATEGY_DESCRIPTIONS,
    STRATEGY_LABELS,
    get_multiplier,
    get_rationale,
    resolve_usage_type_group_multiplier,
)
from .core.rounding import round_percent, round_usd
from .core.summary import SummaryError, generate_summary
from .models import (
    AnalyzeDrStrategyResponse,
    AnalyzeRow,
    Calculation,
    ComparedServiceRow,
    CompareDrStrategiesResponse,
    ErrorInfo,
    ListStrategiesResponse,
    PerAccountAnalysis,
    PerAccountTotalsRow,
    PerStrategyAccountTotals,
    PerStrategyComparedCost,
    PerStrategyTotals,
    Strategy,
    TopNRollup,
    Totals,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error handling - structured exception class plus next-steps lookup table
# ---------------------------------------------------------------------------


# :class:`ToolError` is defined in
# :mod:`dr_cost_estimator_mcp.core.errors` so core-layer modules
# (``aws_costs``) can raise it without creating a circular dependency
# back into this module. It is imported above and re-exported here
# unchanged so ``from .tools import ToolError`` callers continue to
# work.


#: Per-error-code remediation hints surfaced in
#: :class:`ErrorInfo.next_steps`. Strings are plain text only - no
#: Markdown, HTML, shell-expansion, backticks, or URLs.
NEXT_STEPS_TABLE: dict[str, list[str]] = {
    "INVALID_INPUT": [
        "Check the parameter values.",
        (
            "Ensure top_n is in the inclusive range 1 to 500 and "
            "months_back is in 1 to 24."
        ),
    ],
    "AWS_ERROR": [
        "Verify AWS_PROFILE and that the role has ce:GetCostAndUsage permission.",
        (
            "Verify months_back covers a period with non-zero spend in the "
            "targeted accounts."
        ),
    ],
}


def _error_response(exc: ToolError, model_cls):
    """Build a ``success=False`` response of ``model_cls`` from a :class:`ToolError`.

    Resolves the per-code ``next_steps`` from :data:`NEXT_STEPS_TABLE`.
    Always copies the list before passing it to :class:`ErrorInfo` so
    callers cannot mutate the table in place.
    """
    next_steps = list(NEXT_STEPS_TABLE.get(exc.code, []))
    return model_cls(
        success=False,
        error=ErrorInfo(code=exc.code, message=exc.message, next_steps=next_steps),
    )


# ---------------------------------------------------------------------------
# Input handling - AWS Cost Explorer loader
# ---------------------------------------------------------------------------


def _load_from_aws(
    months_back: int,
    linked_account_ids: Optional[list[str]],
    profile: Optional[str],
    region: Optional[str],
    client: Optional[object] = None,
    tag_filters: Optional[dict[str, list[str]]] = None,
) -> dict:
    """Fetch CE spend split by usage type group.

    Wraps :func:`fetch_services_from_aws` with input validation and
    translates every failure mode (bad args, boto3 credential / client
    errors, empty result set) into a :class:`ToolError` with an
    appropriate code so the two AWS tools never leak a raw boto3
    exception to the MCP client.

    The return value is a dict with keys ``utg_rows``,
    ``aggregated_rows``, ``warnings``, and ``utg_resolved_services``
    (see :func:`fetch_services_from_aws`).
    """
    if not (1 <= months_back <= 24):
        raise ToolError(
            "INVALID_INPUT",
            "months_back must be between 1 and 24 (inclusive).",
        )
    if linked_account_ids is not None:
        if not isinstance(linked_account_ids, list) or not all(
            isinstance(a, str) and a.strip() for a in linked_account_ids
        ):
            raise ToolError(
                "INVALID_INPUT",
                "linked_account_ids must be a list of non-empty strings.",
            )
        for acct in linked_account_ids:
            if not (acct.isdigit() and len(acct) == 12):
                raise ToolError(
                    "INVALID_INPUT",
                    (
                        f"linked_account_ids entry {acct!r} is not a 12-digit "
                        "AWS account id."
                    ),
                )

    try:
        services = fetch_services_from_aws(
            months_back=months_back,
            linked_account_ids=linked_account_ids,
            profile=profile,
            region=region,
            client=client,
            tag_filters=tag_filters,
        )
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any boto3 / credential error
        # Intentional broad catch: boto3 raises many distinct exception
        # classes (ClientError, NoCredentialsError, ProfileNotFound,
        # EndpointConnectionError, ...) and we want one uniform
        # AWS_ERROR code over the MCP boundary. The original class name
        # is preserved in the message for the LLM to surface.
        raise ToolError(
            "AWS_ERROR",
            f"{type(exc).__name__}: {exc}",
        )

    if not services.get("utg_rows") and not services.get("aggregated_rows"):
        message = (
            "Cost Explorer returned zero DR-eligible services for the "
            "requested window. Verify the months_back, linked_account_ids, "
            "and that the profile has ce:GetCostAndUsage permission."
        )
        if tag_filters:
            keys = ", ".join(sorted(tag_filters))
            message += (
                f" The query was scoped to tag key(s) {keys}: confirm each "
                "is ACTIVATED as a cost allocation tag in the Billing "
                "console (activation only applies to spend from that point "
                "on, with up to 24 hours of delay) and that the tag values "
                "match exactly (tag matching is case-sensitive)."
            )
        raise ToolError("AWS_ERROR", message)

    return services


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


# Confidence ordering from best to worst. Lower index = higher confidence.
_CONFIDENCE_ORDERING: list[str] = ["high", "medium-high", "medium", "low"]

# Inclusive bounds for the warm_standby_capacity tool parameter.
_WARM_CAPACITY_MIN = 0.1
_WARM_CAPACITY_MAX = 1.0


def _validate_warm_standby_capacity(value: float) -> Optional[ToolError]:
    """Return a ``ToolError`` when the warm-capacity parameter is invalid."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ToolError(
            "INVALID_INPUT",
            "warm_standby_capacity must be a number between "
            f"{_WARM_CAPACITY_MIN} and {_WARM_CAPACITY_MAX} (inclusive).",
        )
    if not (_WARM_CAPACITY_MIN <= value <= _WARM_CAPACITY_MAX):
        return ToolError(
            "INVALID_INPUT",
            f"warm_standby_capacity must be between {_WARM_CAPACITY_MIN} "
            f"and {_WARM_CAPACITY_MAX} (inclusive); got {value}.",
        )
    return None


def _append_warm_capacity_note(
    warnings: list[str], warm_standby_capacity: float
) -> None:
    """Make a non-default warm-capacity assumption visible in the response."""
    if warm_standby_capacity != DEFAULT_WARM_CAPACITY:
        warnings.append(
            f"warm_standby_capacity={warm_standby_capacity:g}: warm-standby "
            "multipliers for fleet-scaled compute services (EC2, EBS, ECS, "
            "EMR, server-compute category) were rescaled from the 50% "
            "baseline; other services are unaffected."
        )


# Bounds for the tag_filters parameter, keeping the composed Cost
# Explorer Filter expression well inside API limits.
_TAG_FILTER_MAX_KEYS = 10
_TAG_FILTER_MAX_VALUES_PER_KEY = 20


def _validate_tag_filters(
    value: Optional[dict[str, list[str]]],
) -> Optional[ToolError]:
    """Return a ``ToolError`` when the tag_filters parameter is invalid."""
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        return ToolError(
            "INVALID_INPUT",
            "tag_filters must be a non-empty object mapping a cost "
            'allocation tag key to a list of values, e.g. {"Project": '
            '["checkout", "payments"]}.',
        )
    if len(value) > _TAG_FILTER_MAX_KEYS:
        return ToolError(
            "INVALID_INPUT",
            f"tag_filters supports at most {_TAG_FILTER_MAX_KEYS} tag keys; "
            f"got {len(value)}.",
        )
    for key, values in value.items():
        if not isinstance(key, str) or not key.strip():
            return ToolError(
                "INVALID_INPUT",
                "tag_filters keys must be non-empty strings.",
            )
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, str) and v.strip() for v in values)
        ):
            return ToolError(
                "INVALID_INPUT",
                f"tag_filters[{key!r}] must be a non-empty list of "
                "non-empty string values.",
            )
        if len(values) > _TAG_FILTER_MAX_VALUES_PER_KEY:
            return ToolError(
                "INVALID_INPUT",
                f"tag_filters[{key!r}] supports at most "
                f"{_TAG_FILTER_MAX_VALUES_PER_KEY} values; got {len(values)}.",
            )
    return None


def _append_tag_scope_note(
    warnings: list[str], tag_filters: Optional[dict[str, list[str]]]
) -> None:
    """Make a tag-scoped estimate's blind spot visible in the response.

    Tag filters match only spend that CARRIES the tag: any untagged
    resource belonging to the workload is silently absent from the
    estimate, so a workload with incomplete tagging is underestimated.
    """
    if not tag_filters:
        return
    scope = "; ".join(
        f"{key}={'|'.join(values)}" for key, values in sorted(tag_filters.items())
    )
    warnings.append(
        f"Estimate scoped to cost allocation tags [{scope}]. Only spend "
        "carrying these tags is included: untagged resources that belong "
        "to the workload are NOT counted, so incomplete tagging "
        "understates the estimate. Tag keys must be activated as cost "
        "allocation tags in the Billing console."
    )


def _reaggregate_utg_rows(
    utg_rows: list[dict],
    unsupported_rows: list[dict] | None = None,
) -> list[dict]:
    """Re-aggregate per-usage-type-group rows back to service level.

    After resolving per-UTG-row multipliers, this function sums
    ``dr_additional_cost`` and ``monthly_cost`` back to the service level,
    computes the effective multiplier as
    ``sum(dr_additional_cost) / current_cost``, and selects the lowest
    confidence among constituent rows for the service-level row.

    Finally, merges UTG-resolved service rows with unsupported-service rows
    (those that bypass the UTG path) into a unified list.

    Parameters
    ----------
    utg_rows:
        A list of per-UTG-row dicts, each with keys:
        ``service``, ``usage_type_group``, ``monthly_cost``,
        ``dr_additional_cost``, ``multiplier``, ``confidence``, ``source``.
    unsupported_rows:
        Optional list of service-level dicts for services that did not go
        through the UTG path. Each dict should have at minimum:
        ``service``, ``monthly_cost``, ``dr_additional_cost``,
        ``multiplier``, ``confidence``.

    Returns
    -------
    list[dict]
        A unified list of service-level dicts, each with:
        ``service``, ``monthly_cost`` (sum), ``dr_additional_cost`` (sum),
        ``effective_multiplier`` (dr_additional_cost / monthly_cost),
        ``confidence`` (lowest among constituent groups).
    """
    from collections import defaultdict

    # Group UTG rows by service
    service_groups: dict[str, list[dict]] = defaultdict(list)
    for row in utg_rows:
        service_groups[row["service"]].append(row)

    aggregated: list[dict] = []
    for service, rows in service_groups.items():
        total_monthly_cost = sum(r["monthly_cost"] for r in rows)
        total_dr_additional_cost = sum(r["dr_additional_cost"] for r in rows)

        # Effective multiplier: dr_additional_cost / current_cost
        if total_monthly_cost > 0:
            effective_multiplier = total_dr_additional_cost / total_monthly_cost
        else:
            effective_multiplier = 0.0

        # Select lowest confidence among constituent rows
        # Ordering: high > medium-high > medium > low (lower index = better)
        worst_confidence = "high"
        worst_index = 0
        for r in rows:
            row_confidence = r["confidence"]
            try:
                idx = _CONFIDENCE_ORDERING.index(row_confidence)
            except ValueError:
                # Unknown confidence level treated as worst
                idx = len(_CONFIDENCE_ORDERING)
            if idx > worst_index:
                worst_index = idx
                worst_confidence = row_confidence

        aggregated.append({
            "service": service,
            "monthly_cost": total_monthly_cost,
            "dr_additional_cost": total_dr_additional_cost,
            "effective_multiplier": effective_multiplier,
            "confidence": worst_confidence,
            "confidence_source": f"usage-type-group split for {service}",
        })

    # Merge with unsupported-service rows
    if unsupported_rows:
        aggregated.extend(unsupported_rows)

    # Sort unified list by monthly_cost descending (highest cost first)
    aggregated.sort(key=lambda r: r.get("monthly_cost", 0), reverse=True)

    return aggregated


# Maximum length for rationale strings.
_RATIONALE_MAX_CHARS: int = 300


def _build_utg_rationale(utg_row_dicts: list[dict]) -> str:
    """Build a rationale string for a service resolved via usage-type-group split.

    Parameters
    ----------
    utg_row_dicts:
        A list of per-UTG-row dicts, each with keys:
        ``usage_type_group`` (str) - the suffix name (e.g. "Running Hours"),
        ``multiplier`` (float), and ``monthly_cost`` (float).

    Returns
    -------
    str
        A rationale string matching the pattern:
        ``"Split by usage type group: <group1> at <mult1>, <group2> at <mult2>, ...
        Dominant cost driver: <group_with_highest_cost>."``
        Truncated to 300 characters maximum.
    """
    if not utg_row_dicts:
        return ""

    # Identify dominant cost driver (group with highest monthly_cost)
    dominant_group = max(utg_row_dicts, key=lambda r: r["monthly_cost"])
    dominant_name = dominant_group["usage_type_group"]

    # Format multiplier to 2 decimal places
    def _fmt_mult(m: float) -> str:
        return f"{m:.2f}"

    # Build the group list portion
    group_parts = [
        f"{r['usage_type_group']} at {_fmt_mult(r['multiplier'])}"
        for r in utg_row_dicts
    ]

    suffix = f" Dominant cost driver: {dominant_name}."
    prefix = "Split by usage type group: "

    # Try full string first
    groups_str = ", ".join(group_parts)
    full = f"{prefix}{groups_str}.{suffix}"

    if len(full) <= _RATIONALE_MAX_CHARS:
        return full

    # Truncate by reducing the number of groups listed until it fits
    for count in range(len(group_parts) - 1, 0, -1):
        truncated_groups = ", ".join(group_parts[:count])
        candidate = f"{prefix}{truncated_groups}, ...{suffix}"
        if len(candidate) <= _RATIONALE_MAX_CHARS:
            return candidate

    # Fallback: minimal rationale with just the dominant cost driver
    minimal = f"{prefix}...{suffix}"
    if len(minimal) <= _RATIONALE_MAX_CHARS:
        return minimal

    # Ultimate fallback: truncate hard at 300 chars
    return minimal[:_RATIONALE_MAX_CHARS]


def _maybe_apply_top_n(
    services: list[dict], top_n: int, include_all_services: bool
):
    """Apply portfolio-scope Top-N or pass through with a no-op result.

    When ``include_all_services`` is true the survivor list is the full
    sorted-by-cost-desc input with no rollup row, and the rollup
    metadata is empty / zero. Centralised here so analyze and compare
    builders use identical scope logic.
    """
    if include_all_services:
        sorted_svcs = sorted(
            services, key=lambda x: x["monthly_cost"], reverse=True
        )
        # Mirror the TopNRolloverResult shape returned by ``apply_top_n``
        # so callers can use it uniformly.
        return TopNRolloverResult(
            survivors=sorted_svcs,
            rolled_up_count=0,
            rolled_up_total_cost=0.0,
            rolled_up_sample=[],
            rolled_up_service_names=set(),
        )
    return apply_top_n(services, top_n)


def _build_analyze_rows(rows_raw: list[dict]) -> list[AnalyzeRow]:
    """Convert ``compute_estimates`` rows into :class:`AnalyzeRow` instances.

    Rounding happens here at the Pydantic-construction boundary.
    The unrounded ``current_cost`` and
    ``dr_additional_cost`` are retained on the returned ``AnalyzeRow``
    objects only after one round-trip through :func:`round_usd`.
    """
    out: list[AnalyzeRow] = []
    for row in rows_raw:
        current_cost = round_usd(row["current_cost"])
        dr_additional = round_usd(row["dr_additional_cost"])
        total_with = round_usd(row["total_with_dr"])
        out.append(
            AnalyzeRow(
                service=row["service"],
                current_cost=current_cost,
                calculation=Calculation(
                    multiplier=float(row["multiplier"]),
                    dr_additional_cost=dr_additional,
                    total_with_dr=total_with,
                ),
                confidence=row["confidence"],
                confidence_source=row["confidence_source"],
                rationale=row["rationale"],
            )
        )
    return out


def _confidence_rows_from_raw(
    rows_raw: list[dict],
) -> list[tuple[str, float, ConfidenceLevel]]:
    """Project a ``compute_estimates`` row list into the confidence-input shape.

    The :class:`Confidence_Reporter` expects
    ``(service_name, current_cost_unrounded, confidence_level)`` triples
    so the dollar-weighted percentages anchor on the unrounded
    denominator.
    """
    return [
        (row["service"], float(row["current_cost"]), row["confidence"])
        for row in rows_raw
    ]


def _build_top_n_rollup(topn_result) -> Optional[TopNRollup]:
    """Materialize a :class:`TopNRollup` from a ``TopNRolloverResult``.

    Returns ``None`` when no services were rolled up.
    """
    if topn_result.rolled_up_count <= 0:
        return None
    sample = [
        (name, round_usd(cost)) for name, cost in topn_result.rolled_up_sample
    ]
    return TopNRollup(
        rolled_up_services_count=topn_result.rolled_up_count,
        rolled_up_services_sample=sample,
        rolled_up_total_cost=round_usd(topn_result.rolled_up_total_cost),
    )


def _wrap_summary(
    payload, kind: str, warnings: list[str]
) -> str:
    """Run :func:`generate_summary` and apply the documented fallback.

    On any :class:`SummaryError`, returns the literal fallback string
    and appends a ``warnings`` entry naming the failure class.
    The ``warnings`` list is mutated in place.
    """
    try:
        return generate_summary(payload, kind)
    except SummaryError as exc:
        warnings.append(f"summary_generator_failed: {type(exc).__name__}")
        return "Summary unavailable; see structured fields for full results."


# ---------------------------------------------------------------------------
# Usage-type-group processing helper
# ---------------------------------------------------------------------------


def _process_utg_result(
    utg_result: dict,
    strategy: str,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> tuple[list[dict], list[str]]:
    """Process usage-type-group fetch result into pre-resolved service rows.

    Takes the dict returned by :func:`fetch_services_from_aws` and
    produces a unified list of service-level dicts ready for the
    response builders, bypassing their internal multiplier resolution.

    For each UTG row, resolves the multiplier via
    :func:`resolve_usage_type_group_multiplier`. When the resolver
    returns ``None`` (unmatched suffix), falls back to the service-level
    blended multiplier via :func:`get_multiplier`.

    Returns
    -------
    tuple[list[dict], list[str]]
        A tuple of (pre_resolved_rows, warnings) where:
        - ``pre_resolved_rows``: list of service-level dicts with keys
          ``service``, ``monthly_cost`` (aka current_cost),
          ``multiplier``, ``dr_additional_cost``, ``total_with_dr``,
          ``confidence``, ``confidence_source``, ``rationale``.
        - ``warnings``: list of warning strings from the UTG fetch plus
          any generated during processing.
    """
    utg_rows = utg_result.get("utg_rows", [])
    aggregated_rows = utg_result.get("aggregated_rows", [])
    warnings = list(utg_result.get("warnings", []))

    # --- Step 1: Resolve multipliers for each UTG row ---
    resolved_utg_rows: list[dict] = []
    for row in utg_rows:
        service_name = row["service"]
        usage_type_group = row["usage_type_group"]
        monthly_cost = row["monthly_cost"]

        # Try usage-type-group resolution first
        utg_resolution = resolve_usage_type_group_multiplier(
            service_name, usage_type_group, strategy
        )

        if utg_resolution is not None:
            multiplier, source = utg_resolution
        else:
            # Unmatched group (including the remainder row): fall back to
            # the blended service-level multiplier.
            multiplier, source = get_multiplier(
                service_name, strategy, warm_standby_capacity
            )

        dr_additional_cost = monthly_cost * multiplier
        confidence, confidence_source = source_to_confidence(
            source, service_name
        )

        resolved_utg_rows.append({
            "service": service_name,
            "usage_type_group": usage_type_group,
            "monthly_cost": monthly_cost,
            "dr_additional_cost": dr_additional_cost,
            "multiplier": multiplier,
            "confidence": confidence,
            "source": source,
        })

    # --- Step 2: Re-aggregate UTG rows to service level ---
    # Also resolve the aggregated_rows (unsupported + fallback services)
    # through the standard multiplier path so they carry the same shape.
    resolved_aggregated: list[dict] = []
    for row in aggregated_rows:
        service_name = row["service"]
        monthly_cost = row["monthly_cost"]
        multiplier, source = get_multiplier(
            service_name, strategy, warm_standby_capacity
        )
        dr_additional_cost = monthly_cost * multiplier
        confidence, confidence_source_str = source_to_confidence(
            source, service_name
        )

        resolved_aggregated.append({
            "service": service_name,
            "current_cost": monthly_cost,
            "monthly_cost": monthly_cost,
            "multiplier": multiplier,
            "dr_additional_cost": dr_additional_cost,
            "total_with_dr": monthly_cost + dr_additional_cost,
            "confidence": confidence,
            "confidence_source": confidence_source_str,
            "rationale": get_rationale(
                service_name, strategy, warm_standby_capacity
            ),
        })

    # --- Step 3: Re-aggregate UTG rows by service ---
    reaggregated = _reaggregate_utg_rows(resolved_utg_rows)

    # --- Step 4: Build final pre-resolved rows with rationale ---
    # For UTG-resolved services, generate rationale from constituent rows.
    from collections import defaultdict

    utg_rows_by_service: dict[str, list[dict]] = defaultdict(list)
    for row in resolved_utg_rows:
        utg_rows_by_service[row["service"]].append(row)

    pre_resolved_rows: list[dict] = []
    for svc_row in reaggregated:
        service_name = svc_row["service"]
        monthly_cost = svc_row["monthly_cost"]
        dr_additional_cost = svc_row["dr_additional_cost"]
        effective_multiplier = svc_row["effective_multiplier"]
        confidence = svc_row["confidence"]
        confidence_source_str = svc_row.get(
            "confidence_source",
            f"usage-type-group split for {service_name}",
        )

        # Build rationale from the constituent UTG rows
        constituent_rows = utg_rows_by_service.get(service_name, [])
        rationale = _build_utg_rationale(constituent_rows)

        pre_resolved_rows.append({
            "service": service_name,
            "current_cost": monthly_cost,
            "monthly_cost": monthly_cost,
            "multiplier": effective_multiplier,
            "dr_additional_cost": dr_additional_cost,
            "total_with_dr": monthly_cost + dr_additional_cost,
            "confidence": confidence,
            "confidence_source": confidence_source_str,
            "rationale": rationale,
        })

    # Merge with resolved aggregated rows
    pre_resolved_rows.extend(resolved_aggregated)

    # Sort by monthly_cost descending (highest cost first)
    pre_resolved_rows.sort(
        key=lambda r: r.get("monthly_cost", 0), reverse=True
    )

    return pre_resolved_rows, warnings


def _process_utg_result_compare(
    utg_result: dict,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> tuple[list[dict], list[str]]:
    """Process UTG fetch result for the compare builder (all strategies).

    Similar to :func:`_process_utg_result` but resolves multipliers for
    ALL four strategies per service, producing rows that carry a
    ``per_strategy_resolution`` dict so the compare builder can read
    pre-computed multipliers for each strategy without re-resolving.

    Returns
    -------
    tuple[list[dict], list[str]]
        A tuple of (services_with_resolution, warnings) where:
        - ``services_with_resolution``: list of service-level dicts with
          ``service``, ``monthly_cost``, and ``per_strategy_resolution``
          mapping strategy_id -> ``{"multiplier", "confidence",
          "confidence_source"}``.
        - ``warnings``: list of warning strings.
    """
    utg_rows = utg_result.get("utg_rows", [])
    aggregated_rows = utg_result.get("aggregated_rows", [])
    warnings = list(utg_result.get("warnings", []))

    from collections import defaultdict

    # For each strategy, resolve UTG rows and re-aggregate.
    # We need per-strategy resolution per service.
    utg_rows_by_service: dict[str, list[dict]] = defaultdict(list)
    for row in utg_rows:
        utg_rows_by_service[row["service"]].append(row)

    # Build per-service per-strategy resolution
    utg_services_resolved: list[dict] = []
    for service_name, service_utg_rows in utg_rows_by_service.items():
        total_monthly_cost = sum(r["monthly_cost"] for r in service_utg_rows)

        per_strategy_resolution: dict[str, dict] = {}
        for sid in STRATEGIES:
            total_dr_additional = 0.0
            worst_confidence = "high"
            worst_idx = 0

            for row in service_utg_rows:
                usage_type_group = row["usage_type_group"]
                monthly_cost = row["monthly_cost"]

                utg_resolution = resolve_usage_type_group_multiplier(
                    service_name, usage_type_group, sid
                )
                if utg_resolution is not None:
                    multiplier, source = utg_resolution
                else:
                    multiplier, source = get_multiplier(
                        service_name, sid, warm_standby_capacity
                    )

                total_dr_additional += monthly_cost * multiplier
                confidence, _ = source_to_confidence(source, service_name)

                try:
                    idx = _CONFIDENCE_ORDERING.index(confidence)
                except ValueError:
                    idx = len(_CONFIDENCE_ORDERING)
                if idx > worst_idx:
                    worst_idx = idx
                    worst_confidence = confidence

            if total_monthly_cost > 0:
                effective_multiplier = total_dr_additional / total_monthly_cost
            else:
                effective_multiplier = 0.0

            per_strategy_resolution[sid] = {
                "multiplier": effective_multiplier,
                "confidence": worst_confidence,
                "confidence_source": (
                    f"usage-type-group split for {service_name}"
                ),
            }

        utg_services_resolved.append({
            "service": service_name,
            "monthly_cost": total_monthly_cost,
            "per_strategy_resolution": per_strategy_resolution,
        })

    # Build per-strategy resolution for aggregated (unsupported/fallback) rows
    aggregated_resolved: list[dict] = []
    for row in aggregated_rows:
        service_name = row["service"]
        monthly_cost = row["monthly_cost"]

        per_strategy_resolution = {}
        for sid in STRATEGIES:
            multiplier, source = get_multiplier(
                service_name, sid, warm_standby_capacity
            )
            confidence, confidence_source_str = source_to_confidence(
                source, service_name
            )
            per_strategy_resolution[sid] = {
                "multiplier": multiplier,
                "confidence": confidence,
                "confidence_source": confidence_source_str,
            }

        aggregated_resolved.append({
            "service": service_name,
            "monthly_cost": monthly_cost,
            "per_strategy_resolution": per_strategy_resolution,
        })

    # Merge and sort
    all_resolved = utg_services_resolved + aggregated_resolved
    all_resolved.sort(key=lambda r: r.get("monthly_cost", 0), reverse=True)

    return all_resolved, warnings


# ---------------------------------------------------------------------------
# Per-account constants - Cost Explorer aggregates do not carry account
# information, so responses synthesize a single portfolio-wide entry.
# ---------------------------------------------------------------------------


_CE_ACCOUNT_ID = CE_SYNTHETIC_ACCOUNT_ID
_CE_ACCOUNT_NAME = CE_SYNTHETIC_ACCOUNT_NAME


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _build_analyze_response(
    *,
    services: list[dict],
    strategy: str,
    top_n: int,
    include_all_services: bool,
    pre_resolved_rows: list[dict],
    extra_warnings: Optional[list[str]] = None,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> AnalyzeDrStrategyResponse:
    """Build the :class:`AnalyzeDrStrategyResponse` from already-loaded data.

    Implements the analyze pipeline:

    1. Apply portfolio-scope Top-N.
    2. Project the pre-resolved rows onto the survivors -> portfolio
       rows. The synthetic Top-N rollup row (absent from the
       pre-resolved set) is resolved via ``compute_estimates`` so its
       rollup-aware confidence and rationale are preserved.
    3. Synthesize a single per-account entry - Cost Explorer aggregates
       do not carry account information.
    4. Build the dollar-weighted confidence summary anchored on
       portfolio rows.
    5. Generate the plain-text summary; on failure, fall back to the
       literal placeholder and append a warning.
    6. Round only at the Pydantic-construction boundary.

    ``pre_resolved_rows`` carries the usage-type-group-resolved rows
    produced by :func:`_process_utg_result`. Each dict must carry
    ``service``, ``current_cost`` (or ``monthly_cost``),
    ``multiplier``, ``dr_additional_cost``, ``total_with_dr``,
    ``confidence``, ``confidence_source``, ``rationale``.
    """
    warnings: list[str] = []
    if extra_warnings:
        warnings.extend(extra_warnings)

    # Step 1: Portfolio Top-N.
    topn = _maybe_apply_top_n(services, top_n, include_all_services)
    surviving_names = {s["service"] for s in topn.survivors}

    # Step 2: Portfolio rows from the pre-resolved set, in survivor
    # order. The synthetic rollup row produced by ``apply_top_n`` is
    # not in the pre-resolved set; resolve it through
    # ``compute_estimates`` (rollup-aware) instead.
    _pre_resolved_by_service = {r["service"]: r for r in pre_resolved_rows}
    portfolio_raw = []
    for svc in topn.survivors:
        row = _pre_resolved_by_service.get(svc["service"])
        if row is None:
            row = compute_estimates([svc], strategy, warm_standby_capacity)[0]
        portfolio_raw.append(row)
    portfolio_rows = _build_analyze_rows(portfolio_raw)

    # Portfolio totals computed from unrounded floats and rounded once.
    total_current = sum(float(r["current_cost"]) for r in portfolio_raw)
    total_dr_add = sum(float(r["dr_additional_cost"]) for r in portfolio_raw)
    total_with = sum(float(r["total_with_dr"]) for r in portfolio_raw)
    portfolio_totals = Totals(
        total_current_cost=round_usd(total_current),
        total_dr_additional_cost=round_usd(total_dr_add),
        total_with_dr=round_usd(total_with),
        dr_as_percent_of_current=round_percent(total_dr_add, total_current),
    )

    # Step 3: Per-account view. Cost Explorer aggregates carry no
    # account information, so synthesize a single entry covering the
    # whole portfolio. The portfolio rollup row is excluded - the
    # rollup appears once at the portfolio scope.
    acct_raw = [
        r for r in pre_resolved_rows if r["service"] in surviving_names
    ]
    acct_rows = _build_analyze_rows(acct_raw)
    acct_total_current = sum(float(r["current_cost"]) for r in acct_raw)
    acct_total_dr_add = sum(float(r["dr_additional_cost"]) for r in acct_raw)
    acct_total_with = sum(float(r["total_with_dr"]) for r in acct_raw)
    per_account_results = [
        PerAccountAnalysis(
            account_id=_CE_ACCOUNT_ID,
            account_name=_CE_ACCOUNT_NAME,
            estimates=acct_rows,
            totals=Totals(
                total_current_cost=round_usd(acct_total_current),
                total_dr_additional_cost=round_usd(acct_total_dr_add),
                total_with_dr=round_usd(acct_total_with),
                dr_as_percent_of_current=round_percent(
                    acct_total_dr_add, acct_total_current
                ),
            ),
        )
    ]

    # Step 4: Confidence summary anchored on portfolio rows. The
    # rollup row (if any) is included with its forced ``low``
    # confidence.
    confidence_summary = build_confidence_summary(
        _confidence_rows_from_raw(portfolio_raw), total_current
    )

    # Top-N rollup section.
    top_n_rollup = _build_top_n_rollup(topn)

    # Empty-estimate warning.
    if not portfolio_rows:
        warnings.append(
            "No DR-eligible services were found after filtering skip-list "
            "items; estimates are empty."
        )

    # Build the response without the summary first so generate_summary
    # can read structured fields off a real model instance.
    response = AnalyzeDrStrategyResponse(
        success=True,
        summary="",
        strategy=strategy,
        strategy_label=STRATEGY_LABELS[strategy],
        estimates=portfolio_rows,
        totals=portfolio_totals,
        per_account=per_account_results,
        confidence_summary=confidence_summary,
        top_n_rollup=top_n_rollup,
        warnings=warnings,
        error=None,
    )

    response.summary = _wrap_summary(response, "analyze", response.warnings)
    return response


def _build_compare_dr_strategies_response(
    *,
    services: list[dict],
    top_n: int,
    include_all_services: bool,
    pre_resolved_services: list[dict],
    extra_warnings: Optional[list[str]] = None,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> CompareDrStrategiesResponse:
    """Build the :class:`CompareDrStrategiesResponse` from already-loaded data.

    Implements the compare pipeline:

    1. Apply portfolio-scope Top-N -> survivors + surviving_names.
    2. For each survivor, read the pre-resolved per-strategy
       multipliers from its ``per_strategy_resolution`` dict and
       translate into a :class:`PerStrategyComparedCost`. The synthetic
       ``"Other Services (...)"`` rollup row is absent from the
       pre-resolved set; its multiplier is resolved via
       :func:`get_multiplier` and its confidence forced to ``"low"``
       with the rollup-aware confidence_source.
    3. Build per-strategy totals over all survivors.
    4. Build per-account totals: Cost Explorer aggregates carry no
       account information, so synthesize a single entry covering the
       whole portfolio.
    5. Build the dollar-weighted confidence summary anchored on the
       warm_standby strategy confidences.
    6. Generate the plain-text summary; on failure fall back to the
       literal placeholder and append a warning.

    ``pre_resolved_services`` carries the usage-type-group-resolved
    rows produced by :func:`_process_utg_result_compare`. Each dict
    must carry ``service``, ``monthly_cost``, and
    ``per_strategy_resolution`` mapping strategy_id ->
    ``{"multiplier", "confidence", "confidence_source"}``.
    """
    warnings: list[str] = []
    if extra_warnings:
        warnings.extend(extra_warnings)

    # Lookup of pre-resolved per-strategy info keyed by service name.
    _utg_resolution_map: dict[str, dict[str, dict]] = {
        svc["service"]: svc.get("per_strategy_resolution", {})
        for svc in pre_resolved_services
    }

    topn = _maybe_apply_top_n(services, top_n, include_all_services)
    surviving_names = {s["service"] for s in topn.survivors}

    # ----- Step 2/3: per-service rows + portfolio totals --------------------

    compared_rows: list[ComparedServiceRow] = []
    accum_add_per_strategy: dict[str, float] = {sid: 0.0 for sid in STRATEGIES}
    accum_total_per_strategy: dict[str, float] = {sid: 0.0 for sid in STRATEGIES}
    portfolio_total_current = 0.0
    # Anchor the dollar-weighted confidence summary on the warm_standby
    # strategy rows so the summary reflects the same confidence breakdown
    # users see in :func:`analyze_dr_strategy(strategy="warm_standby")`.
    confidence_anchor: list[tuple[str, float, ConfidenceLevel]] = []

    for svc in topn.survivors:
        svc_name = svc["service"]
        raw_cost = float(svc["monthly_cost"])
        portfolio_total_current += raw_cost

        per_strategy: dict[str, PerStrategyComparedCost] = {}
        rollup_match = ROLLUP_NAME_RE.match(svc_name)
        is_rollup = rollup_match is not None
        rolled_count = int(rollup_match.group(1)) if is_rollup else 0

        for sid in STRATEGIES:
            # Check for pre-resolved UTG data first.
            utg_res = _utg_resolution_map.get(svc_name, {}).get(sid)
            if utg_res is not None and not is_rollup:
                multiplier = float(utg_res["multiplier"])
                dr_additional_raw = raw_cost * multiplier
                total_with_raw = raw_cost + dr_additional_raw
                confidence = utg_res["confidence"]
                confidence_source = utg_res["confidence_source"]
            else:
                multiplier, source = get_multiplier(
                    svc_name, sid, warm_standby_capacity
                )
                dr_additional_raw = raw_cost * multiplier
                total_with_raw = raw_cost + dr_additional_raw

                if is_rollup:
                    confidence: ConfidenceLevel = "low"
                    confidence_source = (
                        f"Top-N rollup of {rolled_count} services; multiplier is "
                        "the cost-weighted average across rolled-up services"
                    )
                else:
                    confidence, confidence_source = source_to_confidence(
                        source, svc_name
                    )

            per_strategy[sid] = PerStrategyComparedCost(
                multiplier=float(multiplier),
                dr_additional_cost=round_usd(dr_additional_raw),
                total_with_dr=round_usd(total_with_raw),
                confidence=confidence,
                confidence_source=confidence_source,
            )

            accum_add_per_strategy[sid] += dr_additional_raw
            accum_total_per_strategy[sid] += total_with_raw

            if sid == "warm_standby":
                confidence_anchor.append((svc_name, raw_cost, confidence))

        compared_rows.append(
            ComparedServiceRow(
                service=svc_name,
                current_cost=round_usd(raw_cost),
                per_strategy=per_strategy,
            )
        )

    # Round portfolio totals once at the boundary.
    total_current_rounded = round_usd(portfolio_total_current)
    per_strategy_totals: dict[str, PerStrategyTotals] = {}
    for sid in STRATEGIES:
        add_raw = accum_add_per_strategy[sid]
        total_raw = accum_total_per_strategy[sid]
        per_strategy_totals[sid] = PerStrategyTotals(
            total_dr_additional_cost=round_usd(add_raw),
            total_with_dr=round_usd(total_raw),
            dr_as_percent_of_current=round_percent(
                add_raw, portfolio_total_current
            ),
        )

    # ----- Step 4: per-account totals --------------------------------------
    # Cost Explorer aggregates carry no account information, so
    # synthesize a single entry covering the whole portfolio. The
    # portfolio rollup row is excluded - the rollup appears once at the
    # portfolio scope.

    kept = [s for s in services if s["service"] in surviving_names]
    acct_total_current = 0.0
    acct_add_per_strategy: dict[str, float] = {sid: 0.0 for sid in STRATEGIES}
    acct_total_per_strategy: dict[str, float] = {sid: 0.0 for sid in STRATEGIES}
    for svc in kept:
        raw_cost = float(svc["monthly_cost"])
        acct_total_current += raw_cost
        for sid in STRATEGIES:
            # Kept rows are real services and always carry a
            # pre-resolved per-strategy multiplier.
            multiplier = float(_utg_resolution_map[svc["service"]][sid]["multiplier"])
            dr_additional_raw = raw_cost * multiplier
            acct_add_per_strategy[sid] += dr_additional_raw
            acct_total_per_strategy[sid] += raw_cost + dr_additional_raw

    per_strategy_acct: dict[str, PerStrategyAccountTotals] = {}
    for sid in STRATEGIES:
        per_strategy_acct[sid] = PerStrategyAccountTotals(
            total_dr_additional_cost=round_usd(acct_add_per_strategy[sid]),
            total_with_dr=round_usd(acct_total_per_strategy[sid]),
        )

    per_account_totals = [
        PerAccountTotalsRow(
            account_id=_CE_ACCOUNT_ID,
            account_name=_CE_ACCOUNT_NAME,
            total_current_cost=round_usd(acct_total_current),
            per_strategy=per_strategy_acct,
        )
    ]

    # ----- Step 5: confidence summary --------------------------------------

    confidence_summary = build_confidence_summary(
        confidence_anchor, portfolio_total_current
    )

    # Empty-result warning.
    if not compared_rows:
        warnings.append(
            "No DR-eligible services were found after filtering skip-list "
            "items; the comparison is empty."
        )

    response = CompareDrStrategiesResponse(
        success=True,
        summary="",
        total_current_cost=total_current_rounded,
        per_strategy_totals=per_strategy_totals,
        top_services=compared_rows,
        per_account_totals=per_account_totals,
        confidence_summary=confidence_summary,
        warnings=warnings,
        error=None,
    )

    response.summary = _wrap_summary(response, "compare", response.warnings)
    return response


# ---------------------------------------------------------------------------
# Tool class - registers and implements the three MCP tools
# ---------------------------------------------------------------------------


from fastmcp import FastMCP  # noqa: E402  (import grouped for tool registration)
from pydantic import Field  # noqa: E402

from .core.aws_costs import fetch_services_from_aws  # noqa: E402


class DRCostEstimatorTools:
    """MCP adapter that exposes the DR cost tools on a FastMCP server.

    Instantiate once and call :meth:`register` with an initialized
    :class:`~fastmcp.FastMCP` instance during server startup. Each
    ``@mcp.tool`` inner function is a thin delegator to an ``async``
    method on ``self`` so the tool methods remain directly callable
    from unit tests without needing a live MCP runtime.

    Individual tool registrations are wrapped in ``try / except`` so a
    single tool's import or signature error does not take the server
    offline; failures are logged via ``logger.error`` and registration
    continues.
    """

    def register(self, mcp: FastMCP) -> None:
        """Register the MCP tools on the supplied FastMCP instance."""
        # ``list_dr_strategies`` ------------------------------------------------
        try:

            @mcp.tool(name="list_dr_strategies")
            async def list_dr_strategies_tool() -> ListStrategiesResponse:
                """List the four supported DR strategies with labels and descriptions."""
                return await self.list_dr_strategies()

        except Exception as exc:  # noqa: BLE001
            logger.error("list_dr_strategies failed to register: %s", exc)

        # ``compare_dr_strategies`` ----------------------------------
        try:

            @mcp.tool(name="compare_dr_strategies")
            async def compare_dr_strategies_tool(
                months_back: int = Field(
                    default=3,
                    ge=1,
                    le=24,
                    description=(
                        "Number of complete months of Cost Explorer history "
                        "to average across (1-24)."
                    ),
                ),
                linked_account_ids: Optional[list[str]] = Field(
                    default=None,
                    description=(
                        "Optional list of 12-digit AWS account ids to "
                        "restrict the query to."
                    ),
                ),
                profile: Optional[str] = Field(
                    default=None,
                    description=(
                        "Optional AWS named profile. Defaults to the boto3 "
                        "credential chain."
                    ),
                ),
                region: Optional[str] = Field(
                    default=None,
                    description=(
                        "Optional AWS region for the boto3 session. Cost "
                        "Explorer is accessed via us-east-1."
                    ),
                ),
                top_n: int = Field(
                    default=DEFAULT_TOP_N,
                    ge=1,
                    le=500,
                    description=(
                        "Top-N rollup size. Ignored when "
                        "include_all_services is true."
                    ),
                ),
                include_all_services: bool = Field(
                    default=False,
                    description=("When true, skip the Top-N rollup."),
                ),
                warm_standby_capacity: float = Field(
                    default=DEFAULT_WARM_CAPACITY,
                    ge=_WARM_CAPACITY_MIN,
                    le=_WARM_CAPACITY_MAX,
                    description=(
                        "Fraction of primary compute capacity kept warm in "
                        "the DR region for the warm_standby strategy "
                        "(0.1-1.0, default 0.5). Rescales fleet-shaped "
                        "compute services (EC2, EBS, ECS, EMR) only; "
                        "databases, storage, and control-plane fees are "
                        "unaffected."
                    ),
                ),
                tag_filters: Optional[dict[str, list[str]]] = Field(
                    default=None,
                    description=(
                        "Optional cost-allocation-tag scope mapping a tag "
                        'key to accepted values, e.g. {"Project": '
                        '["checkout", "payments"], "Environment": ["prod"]}. '
                        "Keys are ANDed; values within a key are ORed. "
                        "Scopes the estimate to a tagged workload instead "
                        "of the whole account/portfolio. Tag keys must be "
                        "activated as cost allocation tags; untagged "
                        "resources are excluded from the estimate."
                    ),
                ),
            ) -> CompareDrStrategiesResponse:
                """Compare DR cost across all four strategies using live Cost Explorer data."""
                return await self.compare_dr_strategies(
                    months_back=months_back,
                    linked_account_ids=linked_account_ids,
                    profile=profile,
                    region=region,
                    top_n=top_n,
                    include_all_services=include_all_services,
                    warm_standby_capacity=warm_standby_capacity,
                    tag_filters=tag_filters,
                )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "compare_dr_strategies failed to register: %s", exc
            )

        # ``analyze_dr_strategy`` ------------------------------------
        try:

            @mcp.tool(name="analyze_dr_strategy")
            async def analyze_dr_strategy_tool(
                strategy: str = Field(
                    ...,
                    description=(
                        "DR strategy id. One of 'backup_restore', 'pilot_light', "
                        "'warm_standby', 'active_active'."
                    ),
                ),
                months_back: int = Field(
                    default=3,
                    ge=1,
                    le=24,
                    description=(
                        "Number of complete months of Cost Explorer history "
                        "to average across (1-24)."
                    ),
                ),
                linked_account_ids: Optional[list[str]] = Field(
                    default=None,
                    description=(
                        "Optional list of 12-digit AWS account ids to "
                        "restrict the query to."
                    ),
                ),
                profile: Optional[str] = Field(
                    default=None,
                    description=(
                        "Optional AWS named profile. Defaults to the boto3 "
                        "credential chain."
                    ),
                ),
                region: Optional[str] = Field(
                    default=None,
                    description=(
                        "Optional AWS region for the boto3 session. Cost "
                        "Explorer is accessed via us-east-1."
                    ),
                ),
                top_n: int = Field(
                    default=DEFAULT_TOP_N,
                    ge=1,
                    le=500,
                    description=(
                        "Top-N rollup size. Ignored when "
                        "include_all_services is true."
                    ),
                ),
                include_all_services: bool = Field(
                    default=False,
                    description=("When true, skip the Top-N rollup."),
                ),
                warm_standby_capacity: float = Field(
                    default=DEFAULT_WARM_CAPACITY,
                    ge=_WARM_CAPACITY_MIN,
                    le=_WARM_CAPACITY_MAX,
                    description=(
                        "Fraction of primary compute capacity kept warm in "
                        "the DR region for the warm_standby strategy "
                        "(0.1-1.0, default 0.5). Rescales fleet-shaped "
                        "compute services (EC2, EBS, ECS, EMR) only; "
                        "databases, storage, and control-plane fees are "
                        "unaffected."
                    ),
                ),
                tag_filters: Optional[dict[str, list[str]]] = Field(
                    default=None,
                    description=(
                        "Optional cost-allocation-tag scope mapping a tag "
                        'key to accepted values, e.g. {"Project": '
                        '["checkout", "payments"], "Environment": ["prod"]}. '
                        "Keys are ANDed; values within a key are ORed. "
                        "Scopes the estimate to a tagged workload instead "
                        "of the whole account/portfolio. Tag keys must be "
                        "activated as cost allocation tags; untagged "
                        "resources are excluded from the estimate."
                    ),
                ),
            ) -> AnalyzeDrStrategyResponse:
                """Analyze DR cost for a single strategy using live Cost Explorer data."""
                return await self.analyze_dr_strategy(
                    strategy=strategy,
                    months_back=months_back,
                    linked_account_ids=linked_account_ids,
                    profile=profile,
                    region=region,
                    top_n=top_n,
                    include_all_services=include_all_services,
                    warm_standby_capacity=warm_standby_capacity,
                    tag_filters=tag_filters,
                )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "analyze_dr_strategy failed to register: %s", exc
            )

    # -- async tool methods --------------------------------------------------

    async def list_dr_strategies(self) -> ListStrategiesResponse:
        """Return the four supported DR strategies in canonical order."""
        strategies = [
            Strategy(
                id=sid,
                label=STRATEGY_LABELS[sid],
                description=STRATEGY_DESCRIPTIONS[sid],
            )
            for sid in STRATEGIES
        ]
        return ListStrategiesResponse(success=True, strategies=strategies)

    async def compare_dr_strategies(
        self,
        months_back: int,
        linked_account_ids: Optional[list[str]],
        profile: Optional[str],
        region: Optional[str],
        top_n: int,
        include_all_services: bool,
        warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
        tag_filters: Optional[dict[str, list[str]]] = None,
        client: Optional[object] = None,
    ) -> CompareDrStrategiesResponse:
        """Compare all four DR strategies using live Cost Explorer data.

        ``client`` is a test seam: when supplied, boto3 session / client
        construction is skipped and the provided object is called
        directly. Production callers leave it ``None``.

        Supported services (RDS, OpenSearch, ELB) are always split by
        usage type group and resolved with granular per-cost-component
        multipliers.

        ``warm_standby_capacity`` (0.1-1.0, default 0.5) rescales the
        warm-standby multiplier for fleet-shaped compute services (EC2,
        EBS, ECS, EMR, and the server-compute category) that assume
        "run X% of the primary fleet" in DR.

        ``tag_filters`` scopes the whole estimate to spend carrying the
        given cost allocation tags (tag key -> accepted values; keys
        are ANDed, values within a key ORed), turning the portfolio
        estimate into a per-workload one.
        """
        capacity_error = _validate_warm_standby_capacity(warm_standby_capacity)
        if capacity_error is not None:
            logger.info("ToolError: %s", capacity_error.message)
            return _error_response(capacity_error, CompareDrStrategiesResponse)
        tag_error = _validate_tag_filters(tag_filters)
        if tag_error is not None:
            logger.info("ToolError: %s", tag_error.message)
            return _error_response(tag_error, CompareDrStrategiesResponse)

        try:
            utg_result = _load_from_aws(
                months_back=months_back,
                linked_account_ids=linked_account_ids,
                profile=profile,
                region=region,
                client=client,
                tag_filters=tag_filters,
            )
        except ToolError as exc:
            logger.info("%s: %s", type(exc).__name__, exc.message)
            return _error_response(exc, CompareDrStrategiesResponse)

        # Process the usage-type-group fetch result into pre-resolved rows.
        pre_resolved_services, extra_warnings = _process_utg_result_compare(
            utg_result, warm_standby_capacity
        )
        _append_warm_capacity_note(extra_warnings, warm_standby_capacity)
        _append_tag_scope_note(extra_warnings, tag_filters)
        # Build a flat service list for Top-N from the pre-resolved data.
        services = [
            {"service": s["service"], "monthly_cost": s["monthly_cost"]}
            for s in pre_resolved_services
        ]

        return _build_compare_dr_strategies_response(
            services=services,
            top_n=top_n,
            include_all_services=include_all_services,
            pre_resolved_services=pre_resolved_services,
            extra_warnings=extra_warnings,
            warm_standby_capacity=warm_standby_capacity,
        )

    async def analyze_dr_strategy(
        self,
        strategy: str,
        months_back: int,
        linked_account_ids: Optional[list[str]],
        profile: Optional[str],
        region: Optional[str],
        top_n: int,
        include_all_services: bool,
        warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
        tag_filters: Optional[dict[str, list[str]]] = None,
        client: Optional[object] = None,
    ) -> AnalyzeDrStrategyResponse:
        """Analyze DR cost for a single strategy using live Cost Explorer data.

        ``client`` is a test seam: when supplied, boto3 session / client
        construction is skipped and the provided object is called
        directly. Production callers leave it ``None``.

        Supported services (RDS, OpenSearch, ELB) are always split by
        usage type group and resolved with granular per-cost-component
        multipliers.

        ``warm_standby_capacity`` (0.1-1.0, default 0.5) rescales the
        warm-standby multiplier for fleet-shaped compute services (EC2,
        EBS, ECS, EMR, and the server-compute category) that assume
        "run X% of the primary fleet" in DR.

        ``tag_filters`` scopes the whole estimate to spend carrying the
        given cost allocation tags (tag key -> accepted values; keys
        are ANDed, values within a key ORed), turning the portfolio
        estimate into a per-workload one.
        """
        if strategy not in STRATEGIES:
            exc = ToolError(
                "INVALID_INPUT",
                (
                    f"Unsupported strategy {strategy!r}. "
                    f"Supported strategy ids: {', '.join(STRATEGIES)}."
                ),
            )
            logger.info("%s: %s", type(exc).__name__, exc.message)
            return _error_response(exc, AnalyzeDrStrategyResponse)

        capacity_error = _validate_warm_standby_capacity(warm_standby_capacity)
        if capacity_error is not None:
            logger.info("ToolError: %s", capacity_error.message)
            return _error_response(capacity_error, AnalyzeDrStrategyResponse)
        tag_error = _validate_tag_filters(tag_filters)
        if tag_error is not None:
            logger.info("ToolError: %s", tag_error.message)
            return _error_response(tag_error, AnalyzeDrStrategyResponse)

        try:
            utg_result = _load_from_aws(
                months_back=months_back,
                linked_account_ids=linked_account_ids,
                profile=profile,
                region=region,
                client=client,
                tag_filters=tag_filters,
            )
        except ToolError as exc:
            logger.info("%s: %s", type(exc).__name__, exc.message)
            return _error_response(exc, AnalyzeDrStrategyResponse)

        # Process the usage-type-group fetch result into pre-resolved rows.
        pre_resolved_rows, extra_warnings = _process_utg_result(
            utg_result, strategy, warm_standby_capacity
        )
        _append_warm_capacity_note(extra_warnings, warm_standby_capacity)
        _append_tag_scope_note(extra_warnings, tag_filters)
        # Build a flat service list for Top-N from the pre-resolved data.
        services = [
            {"service": s["service"], "monthly_cost": s["monthly_cost"]}
            for s in pre_resolved_rows
        ]

        return _build_analyze_response(
            services=services,
            strategy=strategy,
            top_n=top_n,
            include_all_services=include_all_services,
            pre_resolved_rows=pre_resolved_rows,
            extra_warnings=extra_warnings,
            warm_standby_capacity=warm_standby_capacity,
        )
