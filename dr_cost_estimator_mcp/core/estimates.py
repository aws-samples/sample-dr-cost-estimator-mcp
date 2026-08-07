# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Per-service DR cost estimation and portfolio Top-N rollup.

Takes the ``{"service": ..., "monthly_cost": ...}`` rows produced by
:mod:`dr_cost_estimator_mcp.core.aws_costs` and turns them into
per-service DR estimate rows (multiplier, additional cost, confidence,
rationale), with an optional portfolio-scope Top-N cut that rolls the
tail into a single synthetic "Other Services (<count> services)" row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from dr_cost_estimator_mcp.consts import MAX_SKIPPED_NAMES_RETURNED
from dr_cost_estimator_mcp.core.confidence import source_to_confidence
from dr_cost_estimator_mcp.core.multipliers import (
    DEFAULT_WARM_CAPACITY,
    get_multiplier,
    get_rationale,
)

__all__ = [
    "ROLLUP_NAME_RE",
    "TopNRolloverResult",
    "apply_top_n",
    "compute_estimates",
]

#: Matches the synthetic Top-N rollup row name emitted by
#: :func:`apply_top_n`, capturing the rolled-up service count.
ROLLUP_NAME_RE = re.compile(r"^Other Services \((\d+) services\)$")


@dataclass
class TopNRolloverResult:
    """Result of applying portfolio-scope Top-N to a service list.

    Attributes:
        survivors: The Top-N services by ``monthly_cost`` (descending),
            with a synthetic ``"Other Services (<count> services)"``
            row appended when the rolled-up total cost is greater than
            zero. When ``top_n >= len(services)`` no rollup row is
            appended and ``survivors`` is just the sorted input.
        rolled_up_count: Number of services rolled into the synthetic
            row. ``0`` when no rollup occurred.
        rolled_up_total_cost: Sum of ``monthly_cost`` across the
            rolled-up services. ``0.0`` when no rollup occurred.
        rolled_up_sample: List of ``(service_name, monthly_cost)`` pairs
            for the rolled-up services in cost-descending order, capped
            at :data:`MAX_SKIPPED_NAMES_RETURNED` entries.
        rolled_up_service_names: Set of service names that were rolled
            up.
    """

    survivors: list[dict]
    rolled_up_count: int
    rolled_up_total_cost: float
    rolled_up_sample: list[tuple[str, float]]
    rolled_up_service_names: set[str]


def apply_top_n(services: list[dict], top_n: int) -> TopNRolloverResult:
    """Portfolio-scope Top-N cut.

    Sorts ``services`` by ``monthly_cost`` descending and keeps the top
    ``top_n``. The remainder is aggregated into a single synthetic
    rollup row named ``"Other Services (<count> services)"`` and the
    rollup metadata is returned alongside the survivor list so callers
    can populate the analyze response's ``top_n_rollup`` section.

    When ``top_n >= len(services)`` the rollup is empty: ``survivors``
    is the full sorted list and the rollup-metadata fields are zeroed /
    empty.
    """
    sorted_svcs = sorted(services, key=lambda x: x["monthly_cost"], reverse=True)

    if top_n >= len(sorted_svcs):
        return TopNRolloverResult(
            survivors=sorted_svcs,
            rolled_up_count=0,
            rolled_up_total_cost=0.0,
            rolled_up_sample=[],
            rolled_up_service_names=set(),
        )

    top = sorted_svcs[:top_n]
    rest = sorted_svcs[top_n:]
    rolled_up_total_cost = sum(s["monthly_cost"] for s in rest)
    # ``rest`` is already sorted cost-descending because it is a slice
    # of ``sorted_svcs``.
    rolled_up_sample = [
        (s["service"], s["monthly_cost"]) for s in rest
    ][:MAX_SKIPPED_NAMES_RETURNED]
    rolled_up_service_names = {s["service"] for s in rest}

    survivors: list[dict] = list(top)
    if rolled_up_total_cost > 0:
        survivors.append({
            "service": f"Other Services ({len(rest)} services)",
            "monthly_cost": rolled_up_total_cost,
        })

    return TopNRolloverResult(
        survivors=survivors,
        rolled_up_count=len(rest),
        rolled_up_total_cost=rolled_up_total_cost,
        rolled_up_sample=rolled_up_sample,
        rolled_up_service_names=rolled_up_service_names,
    )


def compute_estimates(
    services: list[dict],
    strategy: str,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> list[dict]:
    """Calculate DR cost estimates for each service.

    Each emitted dict carries:

    * ``service`` - original service name (un-normalized).
    * ``current_cost`` - the input ``monthly_cost`` (unrounded).
    * ``multiplier`` - multiplier from
      :func:`~dr_cost_estimator_mcp.core.multipliers.get_multiplier`.
    * ``dr_additional_cost`` - ``current_cost * multiplier`` (unrounded).
    * ``total_with_dr`` - ``current_cost + dr_additional_cost`` (unrounded).
    * ``confidence`` - plain-language confidence level.
    * ``confidence_source`` - human-readable prose describing which
      resolution tier produced the multiplier.
    * ``rationale`` - explanation string for the (service, strategy) pair.

    Special-cases the synthetic Top-N rollup row produced by
    :func:`apply_top_n` (service name matching ``ROLLUP_NAME_RE``):
    its ``confidence`` is forced to ``"low"``, ``confidence_source`` is
    set to a rollup-aware string, and ``rationale`` is set to
    ``"Top-N rollup of <N> services."`` rather than the generic
    "default fallback" wording. The numeric multiplier is still
    resolved via :func:`get_multiplier` so per-row math
    (``dr_additional_cost = current_cost * multiplier``) remains
    consistent with non-rollup rows.
    """
    results: list[dict] = []
    for svc in services:
        service_name = svc["service"]
        current_cost = svc["monthly_cost"]

        multiplier, source = get_multiplier(
            service_name, strategy, warm_standby_capacity
        )
        dr_cost = current_cost * multiplier

        rollup_match = ROLLUP_NAME_RE.match(service_name)
        if rollup_match is not None:
            rolled_up_count = int(rollup_match.group(1))
            confidence: Literal["high", "medium", "low"] = "low"
            confidence_source = (
                f"Top-N rollup of {rolled_up_count} services; multiplier is "
                "the cost-weighted average across rolled-up services"
            )
            rationale = f"Top-N rollup of {rolled_up_count} services."
        else:
            confidence, confidence_source = source_to_confidence(source, service_name)
            rationale = get_rationale(
                service_name, strategy, warm_standby_capacity
            )

        results.append({
            "service": service_name,
            "current_cost": current_cost,
            "multiplier": multiplier,
            "dr_additional_cost": dr_cost,
            "total_with_dr": current_cost + dr_cost,
            "confidence": confidence,
            "confidence_source": confidence_source,
            "rationale": rationale,
        })
    return results
