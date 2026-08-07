# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Summary generator for the DR cost estimator analysis surface.

Hosts ``generate_summary(payload, kind)``, which produces the
deterministic plain-text ``summary`` field returned at the top of every
analysis response.

The function is intentionally pure: it derives the summary string from
data already present on the response payload, performs no I/O, and uses
no clock, environment variable, or random source. Two calls on the same
payload return byte-for-byte identical strings.

On any internal failure (missing required fields, unexpected payload
shape, formatting error) :func:`generate_summary` raises
:class:`SummaryError`. The tool boundary is responsible for catching
that exception, substituting the literal fallback string, and appending
a warning -- this module does not perform that fallback substitution
itself.
"""

from __future__ import annotations

import logging
from typing import Literal, Union

from .. import models as m
from ..consts import CE_SYNTHETIC_ACCOUNT_ID, CE_SYNTHETIC_ACCOUNT_NAME
from .multipliers import STRATEGY_LABELS

logger = logging.getLogger(__name__)


__all__ = [
    "SummaryError",
    "SummaryKind",
    "generate_summary",
]


# Public type alias for the ``kind`` parameter on :func:`generate_summary`.
SummaryKind = Literal["compare", "analyze"]


# --- Exception --------------------------------------------------------------


class SummaryError(Exception):
    """Raised when :func:`generate_summary` cannot produce a summary.

    The tool boundary catches this exception, sets the response
    ``summary`` field to the literal fallback string, and appends a
    ``warnings`` entry naming the failure class. Callers should not
    attempt to recover from a ``SummaryError`` by re-running the
    generator.
    """


# --- Public API -------------------------------------------------------------


def generate_summary(
    payload: Union[m.CompareDrStrategiesResponse, m.AnalyzeDrStrategyResponse],
    kind: SummaryKind,
) -> str:
    """Return a 2-4 sentence plain-text summary for ``payload``.

    Parameters
    ----------
    payload:
        A successful :class:`~dr_cost_estimator_mcp.models.CompareDrStrategiesResponse`
        (when ``kind == "compare"``) or
        :class:`~dr_cost_estimator_mcp.models.AnalyzeDrStrategyResponse`
        (when ``kind == "analyze"``). The payload must already carry the
        structured fields required by the corresponding template; this
        function does not call any other component.
    kind:
        ``"compare"`` for compare-tool payloads, ``"analyze"`` for
        analyze-tool payloads.

    Returns
    -------
    str
        Plain-text summary. Contains no Markdown, HTML, or code-fence
        characters. Two calls on the same payload return byte-equal
        strings.

    Raises
    ------
    SummaryError
        On any internal failure (missing required field, unexpected
        payload shape, formatting error, or unknown ``kind``). The
        caller substitutes the fallback string.
    """
    try:
        if kind == "compare":
            return _compare_template(payload)
        if kind == "analyze":
            return _analyze_template(payload)
        raise SummaryError(f"unknown summary kind: {kind!r}")
    except SummaryError:
        raise
    except Exception as exc:  # pragma: no cover - defensive last-resort
        raise SummaryError(
            f"summary generation failed: {exc.__class__.__name__}: {exc}"
        ) from exc


# --- Template fillers -------------------------------------------------------


def _compare_template(payload: m.CompareDrStrategiesResponse) -> str:
    """Fill the compare-summary template."""
    if not payload.per_strategy_totals:
        raise SummaryError(
            "compare summary requires a non-empty per_strategy_totals map"
        )
    if not payload.top_services:
        raise SummaryError(
            "compare summary requires at least one top_services row"
        )
    if not payload.per_account_totals:
        # The per-account list is always populated on a successful
        # response, even for single-account inputs. An empty list here
        # means the caller built a malformed payload.
        raise SummaryError(
            "compare summary requires per_account_totals to have at least one entry"
        )

    # Scope phrase. Cost Explorer aggregates carry no per-account
    # breakdown, so the per-account list usually holds one SYNTHETIC
    # entry covering the whole query scope - which may span any number
    # of linked accounts. Claiming "Across 1 account" in that case is
    # false whenever the profile is an organization payer, so the
    # summary only states an account COUNT when the entries are real
    # accounts; for the synthetic entry it describes the scope instead.
    accounts = payload.per_account_totals
    is_synthetic_scope = len(accounts) == 1 and (
        accounts[0].account_id == CE_SYNTHETIC_ACCOUNT_ID
        or accounts[0].account_name == CE_SYNTHETIC_ACCOUNT_NAME
    )
    if is_synthetic_scope:
        scope_phrase = (
            "Across all spend visible to the queried Cost Explorer scope"
        )
    else:
        account_count = len(accounts)
        plural = "" if account_count == 1 else "s"
        scope_phrase = f"Across {account_count} account{plural}"
    total_current = float(payload.total_current_cost)

    # Find the strategies with the lowest and highest portfolio-wide DR
    # additional cost. ``min`` / ``max`` pick the first item on ties; the
    # dict's insertion order is preserved deterministically for a given
    # payload, so the result is byte-stable for byte-equal input.
    items = list(payload.per_strategy_totals.items())
    min_id, min_totals = min(items, key=lambda kv: kv[1].total_dr_additional_cost)
    max_id, max_totals = max(items, key=lambda kv: kv[1].total_dr_additional_cost)
    min_label = STRATEGY_LABELS.get(min_id, min_id)
    max_label = STRATEGY_LABELS.get(max_id, max_id)
    min_add = float(min_totals.total_dr_additional_cost)
    max_add = float(max_totals.total_dr_additional_cost)

    # Largest current-cost driver. We pick by ``current_cost`` so the
    # summary highlights the row that dominates spend even when the
    # ``top_services`` list is unsorted.
    top = max(payload.top_services, key=lambda row: row.current_cost)
    top_service = top.service
    top_cost = float(top.current_cost)

    return (
        f"{scope_phrase}, total monthly spend is "
        f"${total_current:,.2f}. Cheapest DR strategy is {min_label} at "
        f"+${min_add:,.2f}/mo; most expensive is {max_label} at "
        f"+${max_add:,.2f}/mo. Largest current-cost driver: {top_service} "
        f"(${top_cost:,.2f}/mo)."
    )


def _analyze_template(payload: m.AnalyzeDrStrategyResponse) -> str:
    """Fill the analyze-summary template."""
    if payload.totals is None:
        raise SummaryError("analyze summary requires totals to be populated")
    if not payload.estimates:
        raise SummaryError(
            "analyze summary requires at least one estimate row"
        )
    if not payload.strategy_label:
        raise SummaryError("analyze summary requires strategy_label")

    strategy_label = payload.strategy_label
    total_current = float(payload.totals.total_current_cost)
    total_dr_add = float(payload.totals.total_dr_additional_cost)
    pct_add = float(payload.totals.dr_as_percent_of_current)

    # Largest DR additional cost driver across the surviving rows.
    top_row = max(
        payload.estimates,
        key=lambda row: row.calculation.dr_additional_cost,
    )
    top_dr_service = top_row.service
    top_dr_cost = float(top_row.calculation.dr_additional_cost)

    # Confidence summary may be absent on payloads constructed by tests
    # that do not exercise the confidence reporter; treat that as 0%.
    if payload.confidence_summary is not None:
        pct_high = float(payload.confidence_summary.percent_cost_high_confidence)
    else:
        pct_high = 0.0

    # When ``percent_cost_high_confidence`` is non-zero, call out the
    # usage-type-group tier explicitly as the highest-fidelity match;
    # when it is zero, keep the generic high-confidence wording so the
    # analyze summary always carries an integer-percent confidence
    # sentence.
    if pct_high > 0.0:
        confidence_sentence = (
            f"{pct_high:.0f}% of your spend resolved at the "
            "usage-type-group tier - the highest-fidelity match."
        )
    else:
        confidence_sentence = (
            f"{pct_high:.0f}% of cost is on high-confidence multipliers."
        )

    return (
        f"Under {strategy_label}, total monthly spend is ${total_current:,.2f} "
        f"with +${total_dr_add:,.2f}/mo additional DR cost "
        f"({pct_add:.1f}% of current). Largest DR cost driver: "
        f"{top_dr_service} (+${top_dr_cost:,.2f}/mo). "
        f"{confidence_sentence}"
    )
