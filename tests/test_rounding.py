# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for :mod:`dr_cost_estimator_mcp.core.rounding`.

Pins down the documented behaviour of the two response-boundary
helpers and the exported tolerance constant:

* ``round_usd(value)`` - positive values, negative values, zero, and the
  half-decimal boundary (Python's ``round`` uses banker's rounding /
  round-half-even, modulo float representation).
* ``round_percent(num, den)`` - zero/negative denominator short-circuits
  to ``0.0``; otherwise computes ``num / den * 100`` rounded to two
  decimals; negative numerators pass through unchanged (no ``abs``).
* ``ROUND_TOLERANCE`` is exported and equals ``0.01``.
"""

from __future__ import annotations

from dr_cost_estimator_mcp.core import rounding
from dr_cost_estimator_mcp.core.rounding import (
    ROUND_TOLERANCE,
    round_percent,
    round_usd,
)


# ---------------------------------------------------------------------------
# ROUND_TOLERANCE constant
# ---------------------------------------------------------------------------


def test_round_tolerance_is_one_cent() -> None:
    # Reconciliation property tests rely on this exact value.
    assert ROUND_TOLERANCE == 0.01


def test_round_tolerance_is_in_module_all() -> None:
    # The constant is part of the documented public surface.
    assert "ROUND_TOLERANCE" in rounding.__all__


# ---------------------------------------------------------------------------
# round_usd
# ---------------------------------------------------------------------------


def test_round_usd_zero_returns_zero() -> None:
    assert round_usd(0.0) == 0.0


def test_round_usd_positive_rounds_to_two_decimals() -> None:
    # Plain truncation case: third decimal is 4, rounds down.
    assert round_usd(1.234) == 1.23
    # Plain rounding-up case: third decimal is 6, rounds up.
    assert round_usd(1.236) == 1.24
    # Already at two decimals: must be returned unchanged.
    assert round_usd(12.34) == 12.34


def test_round_usd_large_positive_value_rounds_to_two_decimals() -> None:
    # Sanity check for portfolio-scale values.
    assert round_usd(1_234_567.8912) == 1_234_567.89
    assert round_usd(1_234_567.8956) == 1_234_567.90


def test_round_usd_negative_passes_through_without_abs() -> None:
    # round_usd must not take an absolute value; negatives must round
    # to two decimals and stay negative.
    assert round_usd(-1.234) == -1.23
    assert round_usd(-1.236) == -1.24
    assert round_usd(-0.005) == round(-0.005, 2)


def test_round_usd_uses_pythons_round_half_even() -> None:
    # round_usd is documented to delegate to ``round(value, 2)``.
    # Float representation makes 1.005 awkward, so we pin down the
    # behaviour by comparing against ``round(value, 2)`` directly.
    for value in (1.005, 2.675, 0.125, 0.135, -1.005, -2.675):
        assert round_usd(value) == round(value, 2)


def test_round_usd_banker_rounding_on_exact_half_boundary() -> None:
    # 0.5 and 1.5 are exactly representable in IEEE-754 binary floats,
    # so banker's rounding (round-half-to-even) shows clearly here.
    # ``round(x, 2)`` on these is just ``x`` since they already have
    # at most one decimal, so we use values whose third decimal is
    # exactly 5 in floating-point terms by scaling.
    # The key invariant: round_usd matches Python's built-in round.
    assert round_usd(0.5) == 0.5
    assert round_usd(1.5) == 1.5
    # 2.5 -> 2.5 (no change at two decimals).
    assert round_usd(2.5) == 2.5


# ---------------------------------------------------------------------------
# round_percent - guard cases
# ---------------------------------------------------------------------------


def test_round_percent_zero_denominator_returns_zero() -> None:
    assert round_percent(100.0, 0.0) == 0.0
    assert round_percent(0.0, 0.0) == 0.0
    # Negative numerator with zero denominator must still return 0.0,
    # not raise ZeroDivisionError.
    assert round_percent(-50.0, 0.0) == 0.0


def test_round_percent_negative_denominator_returns_zero() -> None:
    # The guard is ``denominator <= 0``; negative denominators must
    # also short-circuit to 0.0.
    assert round_percent(100.0, -1.0) == 0.0
    assert round_percent(0.0, -1.0) == 0.0
    assert round_percent(-100.0, -1.0) == 0.0


# ---------------------------------------------------------------------------
# round_percent - positive denominator cases
# ---------------------------------------------------------------------------


def test_round_percent_simple_fraction() -> None:
    # 1 / 4 * 100 == 25.00
    assert round_percent(1.0, 4.0) == 25.0
    # 3 / 4 * 100 == 75.00
    assert round_percent(3.0, 4.0) == 75.0


def test_round_percent_full_and_zero_share() -> None:
    # 100% and 0% are common edge cases for the confidence summary.
    assert round_percent(10.0, 10.0) == 100.0
    assert round_percent(0.0, 10.0) == 0.0


def test_round_percent_rounds_to_two_decimals() -> None:
    # 1 / 3 * 100 == 33.3333... -> 33.33
    assert round_percent(1.0, 3.0) == 33.33
    # 2 / 3 * 100 == 66.6666... -> 66.67
    assert round_percent(2.0, 3.0) == 66.67


def test_round_percent_negative_numerator_passes_through() -> None:
    # The helper must NOT take abs() of the numerator: a negative
    # numerator over a positive denominator yields a negative percent.
    assert round_percent(-1.0, 4.0) == -25.0
    assert round_percent(-1.0, 3.0) == -33.33


def test_round_percent_matches_unrounded_formula_within_cent() -> None:
    # Sanity check that the helper's output tracks num/den*100 within
    # the rounding tolerance for a handful of awkward fractions.
    for num, den in [
        (1.0, 7.0),
        (22.0, 7.0),
        (123.456, 789.0),
        (-50.0, 200.0),
    ]:
        expected = num / den * 100.0
        assert abs(round_percent(num, den) - expected) <= ROUND_TOLERANCE
