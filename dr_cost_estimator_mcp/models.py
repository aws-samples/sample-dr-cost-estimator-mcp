# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Pydantic response models for the AWS DR Cost Estimator MCP tools.

Every response model has a ``success: bool`` field and an optional
``error: Optional[ErrorInfo]`` so clients can discriminate success and
failure shapes uniformly across the tools.

The two top-level analysis response models are
:class:`CompareDrStrategiesResponse` (returned by
``compare_dr_strategies``) and
:class:`AnalyzeDrStrategyResponse` (returned by
``analyze_dr_strategy``).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# --- Error envelope ---------------------------------------------------------


class ErrorInfo(BaseModel):
    """Structured error returned when ``success`` is ``false``."""

    code: Literal[
        "INVALID_INPUT",
        "AWS_ERROR",
    ] = Field(
        ...,
        description=(
            "Machine-readable error code. One of INVALID_INPUT, AWS_ERROR."
        ),
    )
    message: str = Field(
        ...,
        description="Human-readable error message suitable for surfacing to the end user.",
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="1\u20133 plain-text remediation steps.",
    )


# --- list_dr_strategies -----------------------------------------------------


class Strategy(BaseModel):
    """A single supported DR strategy."""

    id: Literal[
        "backup_restore",
        "pilot_light",
        "warm_standby",
        "active_active",
    ] = Field(
        ...,
        description="Strategy identifier used as input to analyze_dr_strategy.",
    )
    label: str = Field(
        ...,
        description="Human-readable strategy name (for example, 'Warm Standby').",
    )
    description: str = Field(
        ...,
        description="One-sentence summary of the strategy's RPO/RTO posture and cost profile.",
    )


class ListStrategiesResponse(BaseModel):
    """Response shape for the ``list_dr_strategies`` tool."""

    success: bool = Field(
        True,
        description="True when the call succeeded. False indicates ``error`` is populated.",
    )
    strategies: list[Strategy] = Field(
        default_factory=list,
        description="Supported DR strategies in canonical order.",
    )
    error: Optional[ErrorInfo] = Field(
        None,
        description="Populated only when ``success`` is False.",
    )


# --- Shared aggregates ------------------------------------------------------


class Totals(BaseModel):
    """Aggregated totals across all estimate rows for a single strategy."""

    total_current_cost: float = Field(
        ...,
        description="Sum of current_cost across all returned estimate rows, in USD.",
    )
    total_dr_additional_cost: float = Field(
        ...,
        description="Sum of dr_additional_cost across all returned estimate rows, in USD.",
    )
    total_with_dr: float = Field(
        ...,
        description="Sum of total_with_dr across all returned estimate rows, in USD.",
    )
    dr_as_percent_of_current: float = Field(
        ...,
        description=(
            "total_dr_additional_cost expressed as a percentage of total_current_cost. "
            "Zero when total_current_cost is zero."
        ),
    )


# --- Confidence -------------------------------------------------------------


class ConfidenceSummary(BaseModel):
    """Dollar-weighted portfolio confidence summary.

    Each ``percent_cost_*_confidence`` field is the corresponding total
    expressed as a percentage of total current cost, rounded to two
    decimals. When total current cost is zero, all four percents are
    ``0.0``.

    The four-tier model maps multiplier source strings to tiers as
    follows:

    * ``usage_type_group:<suffix>`` → ``high`` (usage-type-group match)
    * ``known`` → ``medium-high`` (service-tier match)
    * ``category:<name>`` → ``medium`` (category-tier match)
    * ``default`` → ``low`` (default-tier match or Top-N rollup)
    """

    total_cost_high_confidence: float = Field(
        ...,
        description=(
            "Sum of current_cost across rows whose confidence is 'high', "
            "in USD. High confidence means the multiplier was resolved "
            "at the usage-type-group tier (source "
            "'usage_type_group:<suffix>')."
        ),
    )
    total_cost_medium_high_confidence: float = Field(
        0.0,
        description=(
            "Sum of current_cost across rows whose confidence is "
            "'medium-high', in USD. Medium-high confidence means the "
            "multiplier was resolved at the service tier (source "
            "'known')."
        ),
    )
    total_cost_medium_confidence: float = Field(
        ...,
        description=(
            "Sum of current_cost across rows whose confidence is "
            "'medium', in USD. Medium confidence means the multiplier "
            "was resolved at the category tier (source "
            "'category:<name>')."
        ),
    )
    total_cost_low_confidence: float = Field(
        ...,
        description=(
            "Sum of current_cost across rows whose confidence is 'low', "
            "in USD. Low confidence means the multiplier was resolved "
            "at the default tier (source 'default'), or that the row is "
            "the Top-N rollup."
        ),
    )
    percent_cost_high_confidence: float = Field(
        ...,
        description=(
            "total_cost_high_confidence as a percentage of total current "
            "cost, rounded to two decimals."
        ),
    )
    percent_cost_medium_high_confidence: float = Field(
        0.0,
        description=(
            "total_cost_medium_high_confidence as a percentage of total "
            "current cost, rounded to two decimals."
        ),
    )
    percent_cost_medium_confidence: float = Field(
        ...,
        description=(
            "total_cost_medium_confidence as a percentage of total "
            "current cost, rounded to two decimals."
        ),
    )
    percent_cost_low_confidence: float = Field(
        ...,
        description=(
            "total_cost_low_confidence as a percentage of total current "
            "cost, rounded to two decimals."
        ),
    )
    line_item_count_by_confidence: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Count of distinct rows (line items) resolved at each "
            "confidence tier, keyed by tier name ('high', 'medium-high', "
            "'medium', 'low'). Reports row-count distribution alongside "
            "the dollar-weight distribution above."
        ),
    )
    services_using_default_fallback: list[tuple[str, float]] = Field(
        default_factory=list,
        description=(
            "(service_name, monthly_cost) pairs for services whose "
            "confidence is 'low', sorted by monthly_cost descending, "
            "capped at the first 20 entries."
        ),
    )


# --- Top-N ------------------------------------------------------------------


class TopNRollup(BaseModel):
    """Transparent summary of services rolled into 'Other Services'."""

    rolled_up_services_count: int = Field(
        ...,
        description=(
            "Count of services that did not survive the portfolio-level "
            "Top-N cut and were rolled into the 'Other Services' row."
        ),
    )
    rolled_up_services_sample: list[tuple[str, float]] = Field(
        default_factory=list,
        description=(
            "(service_name, monthly_cost) pairs for rolled-up services, "
            "sorted by monthly_cost descending, capped at the first 20 "
            "entries."
        ),
    )
    rolled_up_total_cost: float = Field(
        ...,
        description=(
            "Sum of monthly_cost across all rolled-up services, in USD, "
            "rounded to two decimals."
        ),
    )


# --- Per-row analysis shape ----------------------


class Calculation(BaseModel):
    """Per-row DR cost calculation."""

    multiplier: float = Field(
        ...,
        description=(
            "DR multiplier. dr_additional_cost = "
            "round(current_cost * multiplier, 2)."
        ),
    )
    dr_additional_cost: float = Field(
        ...,
        description=(
            "Additional monthly cost incurred by running DR for this "
            "service, in USD."
        ),
    )
    total_with_dr: float = Field(
        ...,
        description=(
            "current_cost + dr_additional_cost, in USD, rounded to two "
            "decimals."
        ),
    )


class AnalyzeRow(BaseModel):
    """Per-service row in an analyze_dr_strategy response.

    The multiplier-derived values are grouped under
    :class:`Calculation`, and the row carries plain-language
    ``confidence`` / ``confidence_source`` / ``rationale`` fields.
    """

    service: str = Field(
        ...,
        description="AWS service name as reported by Cost Explorer.",
    )
    current_cost: float = Field(
        ...,
        description="Current monthly cost for this service in USD.",
    )
    calculation: Calculation = Field(
        ...,
        description=(
            "Multiplier-derived values (multiplier, dr_additional_cost, "
            "total_with_dr) for this row."
        ),
    )
    confidence: Literal["high", "medium-high", "medium", "low"] = Field(
        ...,
        description=(
            "Plain-language confidence level. 'high' = usage-type-group "
            "match (source 'usage_type_group:<suffix>'). 'medium-high' = "
            "service-tier match (source 'known'). 'medium' = "
            "category-tier match (source 'category:<name>'). 'low' = "
            "default-tier match (source 'default') or Top-N rollup row."
        ),
    )
    confidence_source: str = Field(
        ...,
        description=(
            "Human-readable source string of the form 'curated multiplier "
            "for <ServiceName>', \"category '<name>' fallback\", "
            "'default fallback', the usage-type-group split wording, or "
            "the Top-N rollup wording."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "Human-readable rationale for the multiplier on this row, "
            "drawn from known_multipliers.json / categories.json / "
            "defaults.json. Never empty. Does not contain the numeric "
            "multiplier."
        ),
    )


# --- Per-account ------------------------------------------------------------


class PerStrategyAccountTotals(BaseModel):
    """Per-account totals for a single DR strategy."""

    total_dr_additional_cost: float = Field(
        ...,
        description=(
            "Sum of dr_additional_cost across this account's services "
            "for this strategy, in USD."
        ),
    )
    total_with_dr: float = Field(
        ...,
        description=(
            "Sum of total_with_dr across this account's services for "
            "this strategy, in USD."
        ),
    )


class PerAccountTotalsRow(BaseModel):
    """One per-account row in a compare_dr_strategies response."""

    account_id: str = Field(
        ...,
        description="AWS account ID (12-digit identifier) for this linked account.",
    )
    account_name: str = Field(
        ...,
        description="Human-readable account name as it appears in the input.",
    )
    total_current_cost: float = Field(
        ...,
        description=(
            "Sum of current monthly cost across all services for this "
            "account, in USD."
        ),
    )
    per_strategy: dict[str, PerStrategyAccountTotals] = Field(
        default_factory=dict,
        description=(
            "Per-strategy totals for this account, keyed by strategy id "
            "('backup_restore', 'pilot_light', 'warm_standby', "
            "'active_active')."
        ),
    )


class PerAccountAnalysis(BaseModel):
    """Per-account section of an analyze_dr_strategy response."""

    account_id: str = Field(
        ...,
        description="AWS account ID (12-digit identifier) for this linked account.",
    )
    account_name: str = Field(
        ...,
        description="Human-readable account name as it appears in the input.",
    )
    estimates: list[AnalyzeRow] = Field(
        default_factory=list,
        description=(
            "Per-service rows scoped to this account, limited to "
            "services that survived the portfolio-level Top-N cut."
        ),
    )
    totals: Totals = Field(
        ...,
        description="Aggregated totals across this account's estimate rows.",
    )


# --- Compare-tool per-strategy aggregates -----------------------------------


class PerStrategyTotals(BaseModel):
    """Portfolio-wide totals for a single DR strategy."""

    total_dr_additional_cost: float = Field(
        ...,
        description=(
            "Sum of dr_additional_cost across all services for this "
            "strategy, in USD."
        ),
    )
    total_with_dr: float = Field(
        ...,
        description=(
            "Sum of total_with_dr across all services for this strategy, "
            "in USD."
        ),
    )
    dr_as_percent_of_current: float = Field(
        ...,
        description=(
            "total_dr_additional_cost for this strategy expressed as a "
            "percentage of total_current_cost. Zero when "
            "total_current_cost is zero."
        ),
    )


class PerStrategyComparedCost(BaseModel):
    """One strategy's cost impact on a single service in the compare tool."""

    multiplier: float = Field(
        ...,
        description="DR multiplier used for this (service, strategy) pair.",
    )
    dr_additional_cost: float = Field(
        ...,
        description=(
            "Additional monthly cost for this service under this "
            "strategy, in USD."
        ),
    )
    total_with_dr: float = Field(
        ...,
        description=(
            "current_cost + dr_additional_cost for this service under "
            "this strategy, in USD."
        ),
    )
    confidence: Literal["high", "medium-high", "medium", "low"] = Field(
        ...,
        description=(
            "Plain-language confidence level for this (service, "
            "strategy) pair. 'high' = usage-type-group match (source "
            "'usage_type_group:<suffix>'). 'medium-high' = service-tier "
            "match (source 'known'). 'medium' = category-tier match "
            "(source 'category:<name>'). 'low' = default-tier match or "
            "Top-N rollup."
        ),
    )
    confidence_source: str = Field(
        ...,
        description=(
            "Human-readable source string for this (service, strategy) "
            "pair (see :class:`AnalyzeRow.confidence_source`)."
        ),
    )


class ComparedServiceRow(BaseModel):
    """Top-N service row in a compare_dr_strategies response."""

    service: str = Field(
        ...,
        description="AWS service name as it appears in the input.",
    )
    current_cost: float = Field(
        ...,
        description="Current monthly cost for this service in USD.",
    )
    per_strategy: dict[str, PerStrategyComparedCost] = Field(
        default_factory=dict,
        description=(
            "Per-strategy estimates for this service, keyed by strategy "
            "id ('backup_restore', 'pilot_light', 'warm_standby', "
            "'active_active')."
        ),
    )


# --- Top-level response models ---------------------------------------------


class CompareDrStrategiesResponse(BaseModel):
    """Response shape for the ``compare_dr_strategies`` tool.

    Lightweight portfolio-wide comparison across all four DR strategies
    in a single call. Per-service detail belongs on
    :class:`AnalyzeDrStrategyResponse` instead.
    """

    success: bool = Field(
        ...,
        description="True when the call succeeded. False indicates ``error`` is populated.",
    )
    summary: str = Field(
        "",
        description=(
            "Plain-text 2-4 sentence summary of the comparison. "
            "Empty string on error."
        ),
    )
    total_current_cost: float = Field(
        0.0,
        description="Sum of current_cost across all returned services, in USD.",
    )
    per_strategy_totals: dict[str, PerStrategyTotals] = Field(
        default_factory=dict,
        description=(
            "Portfolio-wide totals keyed by strategy id for the four "
            "supported strategies."
        ),
    )
    top_services: list[ComparedServiceRow] = Field(
        default_factory=list,
        description=(
            "Portfolio-wide top-N services list, with current cost and "
            "per-strategy DR additional cost on each row."
        ),
    )
    per_account_totals: list[PerAccountTotalsRow] = Field(
        default_factory=list,
        description=(
            "Per-account totals list with one row per account. For "
            "single-account inputs this list contains exactly one entry "
            "whose totals reconcile with the portfolio totals within "
            "rounding tolerance."
        ),
    )
    confidence_summary: Optional[ConfidenceSummary] = Field(
        None,
        description=(
            "Dollar-weighted confidence summary across the portfolio. "
            "Null on error."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal warnings, for example when the summary generator "
            "fell back to its literal placeholder string."
        ),
    )
    error: Optional[ErrorInfo] = Field(
        None,
        description="Populated only when ``success`` is False.",
    )


class AnalyzeDrStrategyResponse(BaseModel):
    """Response shape for the ``analyze_dr_strategy`` tool.

    Single-call deep-dive for a chosen DR strategy: per-service detail
    plus per-account breakdown plus a transparent Top-N rollup, with
    dollar-weighted confidence and per-row rationale on every row.
    """

    success: bool = Field(
        ...,
        description="True when the call succeeded. False indicates ``error`` is populated.",
    )
    summary: str = Field(
        "",
        description=(
            "Plain-text 2-4 sentence summary of the analysis. "
            "Empty string on error."
        ),
    )
    strategy: Optional[str] = Field(
        None,
        description="Strategy id the analysis was produced for. Null on error.",
    )
    strategy_label: Optional[str] = Field(
        None,
        description="Human-readable strategy label. Null on error.",
    )
    estimates: list[AnalyzeRow] = Field(
        default_factory=list,
        description=(
            "Portfolio-level per-service rows. Includes the synthetic "
            "Top-N rollup row when the input contains more than ``top_n`` "
            "DR-eligible services and ``include_all_services`` is false."
        ),
    )
    totals: Optional[Totals] = Field(
        None,
        description="Portfolio totals across ``estimates``. Null on error.",
    )
    per_account: list[PerAccountAnalysis] = Field(
        default_factory=list,
        description=(
            "Per-account sections with per-service detail. For "
            "single-account inputs contains exactly one entry whose "
            "totals reconcile with the portfolio totals within rounding "
            "tolerance."
        ),
    )
    confidence_summary: Optional[ConfidenceSummary] = Field(
        None,
        description=(
            "Dollar-weighted confidence summary across the portfolio. "
            "Null on error."
        ),
    )
    top_n_rollup: Optional[TopNRollup] = Field(
        None,
        description=(
            "Transparent summary of services rolled into the 'Other "
            "Services' row. Null when no rollup occurred or when "
            "``include_all_services`` is true."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal warnings, for example when the summary generator "
            "fell back to its literal placeholder string."
        ),
    )
    error: Optional[ErrorInfo] = Field(
        None,
        description="Populated only when ``success`` is False.",
    )


# Resolve forward references introduced by ``from __future__ import
# annotations`` so Pydantic v2 can build the JSON schema for fields
# whose annotations reference later-defined classes.
PerAccountTotalsRow.model_rebuild()
PerAccountAnalysis.model_rebuild()
ComparedServiceRow.model_rebuild()
CompareDrStrategiesResponse.model_rebuild()
AnalyzeDrStrategyResponse.model_rebuild()
