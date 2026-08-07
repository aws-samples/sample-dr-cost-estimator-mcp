# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Currency_Rounder for the DR cost estimator analysis surface.

Provides ``round_usd(value)`` and ``round_percent(num, den)`` helpers used at
the response boundary so per-account, per-tag, and portfolio totals reconcile
within rounding tolerance. Aggregations stay in unrounded
``float`` space throughout the pipeline; this module is invoked exactly once
per emitted scalar at the Pydantic-construction boundary.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


__all__ = ["ROUND_TOLERANCE", "round_usd", "round_percent"]


#: Absolute tolerance, in USD, used by reconciliation property tests when
#: comparing per-account / per-tag sums against portfolio totals.
ROUND_TOLERANCE = 0.01


def round_usd(value: float) -> float:
    """Round a USD amount to two decimal places.

    Uses Python's built-in :func:`round` (banker's rounding / round-half-even),
    which is acceptable for USD per the design. Callers MUST invoke this
    helper only at the response-construction boundary; intermediate sums
    must operate on the unrounded value.
    """
    return round(value, 2)


def round_percent(numerator: float, denominator: float) -> float:
    """Return ``numerator / denominator * 100`` rounded to two decimals.

    Returns ``0.0`` when ``denominator <= 0`` so callers
    do not need a separate zero-division guard. Negative numerators pass
    through unchanged (no absolute value).
    """
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100.0, 2)
