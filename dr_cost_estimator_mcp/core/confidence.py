# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Confidence reporting for the DR cost estimator analysis surface.

Hosts ``source_to_confidence`` and ``build_confidence_summary``, which
map each row's multiplier source to a plain-language confidence level
and produce the dollar-weighted portfolio confidence summary. The Top-N
rollup row is fed in like any other row; the caller is responsible for
passing it as ``"low"`` confidence.

The four-tier model maps multiplier source strings to confidence tiers
as follows:

* ``usage_type_group:<suffix>`` → ``high`` (usage-type-group match)
* ``known``                     → ``medium-high`` (service-tier match)
* ``category:<name>``           → ``medium`` (category-tier match)
* ``default``                   → ``low`` (default tier or Top-N rollup)

``services_using_default_fallback`` is sorted cost-desc and capped at
:data:`MAX_SKIPPED_NAMES_RETURNED` (20) entries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from dr_cost_estimator_mcp.consts import MAX_SKIPPED_NAMES_RETURNED
from dr_cost_estimator_mcp.core.rounding import round_percent, round_usd

if TYPE_CHECKING:  # pragma: no cover - import only used for type checkers
    from dr_cost_estimator_mcp.models import ConfidenceSummary

logger = logging.getLogger(__name__)


__all__ = [
    "ConfidenceLevel",
    "source_to_confidence",
    "build_confidence_summary",
]


#: Plain-language confidence levels emitted on every per-service row and
#: aggregated by the dollar-weighted portfolio summary. ``medium-high``
#: sits between ``high`` (usage-type-group match) and ``medium``
#: (category-tier match) so that service-tier matches are
#: distinguishable from category fallbacks in the response payload.
ConfidenceLevel = Literal["high", "medium-high", "medium", "low"]


def source_to_confidence(
    source: str, service_name: str
) -> tuple[ConfidenceLevel, str]:
    """Map a multiplier source string to ``(level, prose)``.

    Resolution table:

    * ``"usage_type_group:<suffix>"`` → ``("high",
      f"usage-type-group multiplier for {service_name} ({suffix})")``
    * ``"known"`` → ``("medium-high", f"curated multiplier for
      {service_name}")``
    * ``"category:<name>"`` → ``("medium", f"category '{name}' fallback")``
    * ``"default"`` → ``("low", "default fallback")``

    Any other source string raises :class:`ValueError`. The defensive
    guard keeps the source-string contract from drifting silently when
    callers introduce a new source.

    Each tier returns a non-empty prose string so the surface always has
    a human-readable ``confidence_source`` to render.

    Parameters
    ----------
    source:
        The ``source`` string returned by
        :func:`dr_cost_estimator_mcp.core.multipliers.get_multiplier` or
        :func:`dr_cost_estimator_mcp.core.multipliers.resolve_usage_type_group_multiplier`.
    service_name:
        The original (un-normalized) service name as it appeared in the
        input. Used to render the high / medium-high prose so that
        e.g. ``"Amazon Elastic Compute Cloud"`` reads naturally even when
        the canonical key in ``known_multipliers.json`` is ``"ec2"``.

    Returns
    -------
    tuple[ConfidenceLevel, str]
        The confidence level and the human-readable ``confidence_source``
        string for the row.
    """
    if source.startswith("usage_type_group:"):
        suffix = source[len("usage_type_group:"):]
        return (
            "high",
            f"usage-type-group multiplier for {service_name} ({suffix})",
        )
    if source == "known":
        return "medium-high", f"curated multiplier for {service_name}"
    if source.startswith("category:"):
        category_name = source[len("category:") :]
        return "medium", f"category '{category_name}' fallback"
    if source == "default":
        return "low", "default fallback"
    raise ValueError(
        f"Unrecognized multiplier source {source!r}; expected "
        "'usage_type_group:<suffix>', 'known', 'category:<name>', "
        "or 'default'."
    )


def build_confidence_summary(
    rows: list[tuple[str, float, ConfidenceLevel]],
    total_current_cost: float,
) -> "ConfidenceSummary":
    """Build the dollar-weighted portfolio confidence summary.

    Sums ``current_cost`` across rows by confidence level, expresses
    each level as a percent of ``total_current_cost`` rounded to two
    decimals, reports the per-tier row counts in
    ``line_item_count_by_confidence``, and lists the ``low``-confidence
    services using the default fallback in cost-descending order, capped
    at :data:`MAX_SKIPPED_NAMES_RETURNED`.

    Parameters
    ----------
    rows:
        ``(service_name, current_cost, confidence_level)`` triples for
        every surviving estimate row, including the Top-N rollup row
        which the caller classifies as ``"low"``. ``current_cost`` is
        the **unrounded** monthly cost.
    total_current_cost:
        The unrounded sum of ``current_cost`` across the same row set.
        Passed in explicitly (rather than re-summed here) so that callers
        can keep one canonical denominator for percent calculations.

    Returns
    -------
    ConfidenceSummary
        Pydantic model populated with the eight totals/percent fields,
        the per-tier ``line_item_count_by_confidence`` dict, and
        ``services_using_default_fallback``. When ``total_current_cost``
        is ``<= 0`` all four percent fields are ``0.0``.
    """
    # Local import: importing at function-call time avoids a circular
    # import (the models module pulls in nothing from core/) and keeps
    # this module importable even when ConfidenceSummary's shape is
    # being edited.
    from dr_cost_estimator_mcp.models import ConfidenceSummary

    total_high = 0.0
    total_medium_high = 0.0
    total_medium = 0.0
    total_low = 0.0
    counts: dict[str, int] = {
        "high": 0,
        "medium-high": 0,
        "medium": 0,
        "low": 0,
    }
    low_rows: list[tuple[str, float]] = []

    for service_name, current_cost, level in rows:
        if level == "high":
            total_high += current_cost
            counts["high"] += 1
        elif level == "medium-high":
            total_medium_high += current_cost
            counts["medium-high"] += 1
        elif level == "medium":
            total_medium += current_cost
            counts["medium"] += 1
        elif level == "low":
            total_low += current_cost
            counts["low"] += 1
            low_rows.append((service_name, current_cost))
        else:  # pragma: no cover - Literal narrows this away at type-check time
            raise ValueError(
                f"Unrecognized confidence level {level!r}; expected one of "
                "'high', 'medium-high', 'medium', 'low'."
            )

    # Sort low-confidence rows cost-desc and cap at the shared response
    # ceiling so the field is bounded regardless of input size.
    low_rows.sort(key=lambda pair: pair[1], reverse=True)
    services_using_default_fallback = [
        (name, round_usd(cost))
        for name, cost in low_rows[:MAX_SKIPPED_NAMES_RETURNED]
    ]

    return ConfidenceSummary(
        total_cost_high_confidence=round_usd(total_high),
        total_cost_medium_high_confidence=round_usd(total_medium_high),
        total_cost_medium_confidence=round_usd(total_medium),
        total_cost_low_confidence=round_usd(total_low),
        percent_cost_high_confidence=round_percent(total_high, total_current_cost),
        percent_cost_medium_high_confidence=round_percent(
            total_medium_high, total_current_cost
        ),
        percent_cost_medium_confidence=round_percent(total_medium, total_current_cost),
        percent_cost_low_confidence=round_percent(total_low, total_current_cost),
        line_item_count_by_confidence=counts,
        services_using_default_fallback=services_using_default_fallback,
    )
