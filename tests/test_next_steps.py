# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the :data:`NEXT_STEPS_TABLE` content in
``dr_cost_estimator_mcp.tools``.

Covers the per-error-code remediation hint contract:

* ``INVALID_INPUT`` - a suggestion to check parameter values, including
  the documented ``top_n`` and ``months_back`` ranges.
* ``AWS_ERROR`` - a suggestion to verify ``AWS_PROFILE`` and
  ``ce:GetCostAndUsage`` and a suggestion to verify ``months_back``.
* Every string in the table is plain text only; no Markdown, no
  shell-expansion, and no embedded URLs.

These tests intentionally read the *content* of the strings using
forgiving case-insensitive substring matching so that small wording
edits do not regress the suite as long as the documented intent is
preserved.
"""

from __future__ import annotations

import re

import pytest

from dr_cost_estimator_mcp.tools import NEXT_STEPS_TABLE


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _entries(code: str) -> list[str]:
    """Return the next-steps list for ``code``; assert basic shape."""
    assert code in NEXT_STEPS_TABLE, (
        f"NEXT_STEPS_TABLE is missing the {code!r} key"
    )
    entries = NEXT_STEPS_TABLE[code]
    assert isinstance(entries, list)
    assert 1 <= len(entries) <= 3, (
        f"{code} must define between 1 and 3 next_steps strings; "
        f"got {len(entries)}"
    )
    for s in entries:
        assert isinstance(s, str) and s.strip(), (
            f"{code} contains an empty or non-string next_steps entry: {s!r}"
        )
    return entries


# ---------------------------------------------------------------------------
# Per-code content tests
# ---------------------------------------------------------------------------


def test_next_steps_table_covers_exactly_the_supported_codes() -> None:
    """The table carries exactly the two error codes the tools can emit."""
    assert set(NEXT_STEPS_TABLE) == {"INVALID_INPUT", "AWS_ERROR"}


def test_next_steps_invalid_input_content() -> None:
    """INVALID_INPUT points at parameter values and documented ranges."""
    entries = _entries("INVALID_INPUT")

    # Suggestion to check parameter values.
    check_hit = any(
        re.search(r"\b(check|verify)\b", e, re.IGNORECASE) for e in entries
    )
    assert check_hit, (
        "INVALID_INPUT next_steps must include a suggestion to check the "
        "parameter values. Got: %r" % entries
    )

    # Suggestion covering the documented top_n and months_back ranges.
    range_hit = any(
        ("top_n" in e) and ("months_back" in e) for e in entries
    )
    assert range_hit, (
        "INVALID_INPUT next_steps must mention the top_n and months_back "
        "ranges. Got: %r" % entries
    )


def test_next_steps_aws_error_content() -> None:
    """AWS_ERROR points at AWS_PROFILE/IAM and months_back."""
    entries = _entries("AWS_ERROR")

    # Suggestion to verify AWS_PROFILE and the ce:GetCostAndUsage permission.
    profile_hit = any(
        ("AWS_PROFILE" in e) and ("ce:GetCostAndUsage" in e) for e in entries
    )
    assert profile_hit, (
        "AWS_ERROR next_steps must include a suggestion to verify AWS_PROFILE "
        "and the ce:GetCostAndUsage permission. Got: %r" % entries
    )

    # Suggestion to verify that months_back covers a period with non-zero spend.
    months_hit = any(
        ("months_back" in e)
        and re.search(r"non[-\s]?zero|spend|period", e, re.IGNORECASE)
        for e in entries
    )
    assert months_hit, (
        "AWS_ERROR next_steps must include a suggestion to verify months_back "
        "covers a period with non-zero spend. Got: %r" % entries
    )


# ---------------------------------------------------------------------------
# Plain-text guard across the entire table
# ---------------------------------------------------------------------------


# Substrings that signal Markdown / HTML / shell-expansion / URLs.
# Matches the design's substring blacklist for next_steps: `**`, `__`,
# `<`, `>`, backticks, `$`, `http://`, `https://`.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "**",
    "__",
    "<",
    ">",
    "`",
    "$",
    "http://",
    "https://",
)


@pytest.mark.parametrize(
    "code,entries",
    [(code, NEXT_STEPS_TABLE[code]) for code in sorted(NEXT_STEPS_TABLE)],
)
def test_next_steps_strings_are_plain_text(code: str, entries: list[str]) -> None:
    """Every entry is plain text - no Markdown, shells, URLs."""
    assert isinstance(entries, list) and entries, (
        f"NEXT_STEPS_TABLE[{code!r}] must be a non-empty list"
    )
    for entry in entries:
        assert isinstance(entry, str) and entry.strip(), (
            f"NEXT_STEPS_TABLE[{code!r}] contains a non-string or empty "
            f"entry: {entry!r}"
        )
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in entry, (
                "NEXT_STEPS_TABLE[%r] entry contains forbidden substring %r "
                "(plain text only): %r"
                % (code, forbidden, entry)
            )
