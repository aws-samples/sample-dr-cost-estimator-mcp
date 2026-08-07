# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Property test for source-to-confidence exhaustiveness.

Property: source-to-confidence mapping is exhaustive.

For any source string of one of the four documented shapes
(``usage_type_group:<suffix>``, ``known``, ``category:<name>``,
``default``),
:func:`dr_cost_estimator_mcp.core.confidence.source_to_confidence`
returns the documented confidence tier (``high``, ``medium-high``,
``medium``, ``low`` respectively) and a non-empty prose string.
Any source string not matching one of the four shapes either raises
or maps to ``low`` (per the design's "no other source string shapes
are accepted" rule).
"""

from __future__ import annotations

from typing import Tuple

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from dr_cost_estimator_mcp.core.confidence import (
    ConfidenceLevel,
    source_to_confidence,
)
from dr_cost_estimator_mcp.core.multipliers import SERVICE_CATEGORIES


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
#
# The four shapes are generated separately so each example carries the
# expected tier alongside the input. ``service_name_strategy`` produces
# realistic, non-empty service names; the usage-type-group ``suffix``
# and category ``name`` generators stay alphanumeric so they round-trip
# unchanged through the colon-delimited prose templates in
# ``source_to_confidence``.
#
# Constants referenced by the property:
#
# * ``EXPECTED_LEVELS`` -- the set of tiers the function may return for
#   any input (valid or invalid). This is the four-tier model.
# * ``_SHAPE_TIERS`` -- the documented mapping from shape name to tier.
#   Each generator tags its output with the matching tier so the
#   property test asserts the documented contract directly rather than
#   re-deriving it inside the test.

EXPECTED_LEVELS: set[ConfidenceLevel] = {"high", "medium-high", "medium", "low"}

_SHAPE_TIERS: dict[str, ConfidenceLevel] = {
    "usage_type_group": "high",
    "known": "medium-high",
    "category": "medium",
    "default": "low",
}

# Service-name and identifier fragments used to build well-formed source
# strings. Restricted to printable, non-control characters so failing
# examples are readable and so a generated value never accidentally
# embeds a newline or control byte that would distract from the
# assertion under test.
_SERVICE_NAME_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_."
)
_SUFFIX_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789:_- "
)
_CATEGORY_NAME_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


_service_name_strategy = st.text(
    alphabet=_SERVICE_NAME_ALPHABET, min_size=1, max_size=40
).map(str.strip).filter(lambda s: len(s) >= 1)


def _utg_source_strategy() -> st.SearchStrategy[Tuple[str, ConfidenceLevel, str]]:
    """Generate ``(source, expected_tier, service_name)`` triples for
    ``usage_type_group:<suffix>`` shapes.

    The suffix segment may itself contain colons and spaces (mirroring
    real Cost Explorer usage-type-group values like
    ``RDS: Running Hours``), so the generator allows both inside the
    suffix alphabet. The expected tier is always ``high``.
    """
    suffix = st.text(
        alphabet=_SUFFIX_ALPHABET, min_size=1, max_size=40
    )
    return st.tuples(suffix, _service_name_strategy).map(
        lambda pair: (f"usage_type_group:{pair[0]}", "high", pair[1])
    )


def _known_source_strategy() -> st.SearchStrategy[Tuple[str, ConfidenceLevel, str]]:
    """Generate ``(source, expected_tier, service_name)`` triples for the
    literal ``known`` shape; expected tier is ``medium-high``.
    """
    return _service_name_strategy.map(
        lambda name: ("known", "medium-high", name)
    )


def _category_source_strategy() -> st.SearchStrategy[
    Tuple[str, ConfidenceLevel, str]
]:
    """Generate ``(source, expected_tier, service_name)`` triples for
    ``category:<name>`` shapes; expected tier is ``medium``.

    The category-name segment is sampled from a mix of (a) the canonical
    ``SERVICE_CATEGORIES`` keys actually emitted by
    :func:`classify_service` and (b) arbitrary alphanumeric strings.
    Including arbitrary names guards against the prose template
    drifting if someone later adds a category to ``categories.json``;
    the function is documented to accept any ``category:<name>`` shape.
    """
    canonical_names = sorted(SERVICE_CATEGORIES.keys())
    canonical_branch = (
        st.sampled_from(canonical_names) if canonical_names else st.nothing()
    )
    arbitrary_branch = st.text(
        alphabet=_CATEGORY_NAME_ALPHABET, min_size=1, max_size=30
    )
    name_strategy = st.one_of(canonical_branch, arbitrary_branch)
    return st.tuples(name_strategy, _service_name_strategy).map(
        lambda pair: (f"category:{pair[0]}", "medium", pair[1])
    )


def _default_source_strategy() -> st.SearchStrategy[
    Tuple[str, ConfidenceLevel, str]
]:
    """Generate ``(source, expected_tier, service_name)`` triples for the
    literal ``default`` shape; expected tier is ``low``.
    """
    return _service_name_strategy.map(
        lambda name: ("default", "low", name)
    )


def _valid_source_strategy() -> st.SearchStrategy[
    Tuple[str, ConfidenceLevel, str]
]:
    """Union of the four documented shapes."""
    return st.one_of(
        _utg_source_strategy(),
        _known_source_strategy(),
        _category_source_strategy(),
        _default_source_strategy(),
    )


def _is_valid_shape(source: str) -> bool:
    """Return True iff ``source`` matches one of the four documented shapes.

    The shape contract uses prefix matching for ``usage_type_group:``
    and ``category:`` (so ``usage_type_group:RDS: Running Hours``
    matches the usage-type-group shape even though the suffix segment
    itself contains a colon), and exact-equality for ``known`` and
    ``default``. The payload after ``usage_type_group:`` /
    ``category:`` may be empty -- the implementation accepts an empty
    suffix / category name and still produces the corresponding tier;
    in production the resolver only emits the usage-type-group source
    with a non-empty suffix.
    """
    if source == "known" or source == "default":
        return True
    if source.startswith("usage_type_group:"):
        return True
    if source.startswith("category:"):
        return True
    return False


def _invalid_source_strategy() -> st.SearchStrategy[str]:
    """Generate source strings that do NOT match any of the four shapes.

    The strategy unions several deliberately tricky branches:

    * The empty string.
    * Arbitrary text drawn from a printable alphabet, filtered so the
      result is not accidentally a valid shape.
    * Other tier names that look plausible (``"high"``, ``"medium"``,
      ``"low"``) but are not themselves valid source strings.
    * Edge prefixes that look close to the valid shapes but do not
      match (``"usage_type_group"`` with no colon, ``"engine:foo"``
      from the retired engine tier, ``"categories:bar"``).

    A final ``filter`` re-applies :func:`_is_valid_shape` to every
    candidate so a freak draw that happens to land on ``"known"`` or
    ``"usage_type_group:foo"`` is dropped before reaching the property.
    """
    plausible_negatives = st.sampled_from(
        [
            "",
            "high",
            "medium",
            "medium-high",
            "low",
            "usage_type_group",  # missing colon-delimited payload
            "usage-type-group:foo",  # wrong delimiter style
            "category",
            "engine:foo",  # retired engine tier is no longer valid
            "engine:",
            "categories:bar",
            "DEFAULT",  # case-sensitive: this is NOT the literal "default"
            "Known",
            " usage_type_group:foo",  # leading space breaks the prefix match
            ":usage_type_group:foo",
        ]
    )
    arbitrary = st.text(
        alphabet=st.characters(
            min_codepoint=0x20, max_codepoint=0x7E
        ),
        max_size=40,
    )
    return st.one_of(plausible_negatives, arbitrary).filter(
        lambda s: not _is_valid_shape(s)
    )


# ---------------------------------------------------------------------------
# Property: well-formed source strings map to the documented tier and prose
# ---------------------------------------------------------------------------


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(triple=_valid_source_strategy())
def test_valid_source_maps_to_documented_tier_and_non_empty_prose(
    triple: Tuple[str, ConfidenceLevel, str],
) -> None:
    """Any well-formed source string maps to the documented tier and a
    non-empty prose string.

    For ``source`` of shape ``usage_type_group:<suffix>`` / ``known`` /
    ``category:<name>`` / ``default``, ``source_to_confidence(source,
    service_name)`` returns ``(tier, prose)`` where:

    * ``tier`` equals the documented tier for that shape
      (``high`` / ``medium-high`` / ``medium`` / ``low`` respectively).
    * ``prose`` is a non-empty string.
    """
    source, expected_tier, service_name = triple

    tier, prose = source_to_confidence(source, service_name)

    assert tier == expected_tier, (
        f"source {source!r} with service_name {service_name!r}: "
        f"expected tier {expected_tier!r}, got {tier!r}"
    )
    assert tier in EXPECTED_LEVELS, (
        f"source {source!r}: returned tier {tier!r} is not one of the "
        f"four documented tiers {sorted(EXPECTED_LEVELS)!r}"
    )
    assert isinstance(prose, str), (
        f"source {source!r}: returned prose has type "
        f"{type(prose).__name__}, expected str"
    )
    assert prose.strip(), (
        f"source {source!r} with service_name {service_name!r}: "
        f"returned empty / whitespace-only prose {prose!r}"
    )


# ---------------------------------------------------------------------------
# Property: malformed source strings either raise or map to ``low``
# ---------------------------------------------------------------------------


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(source=_invalid_source_strategy(), service_name=_service_name_strategy)
def test_unrecognized_source_raises_or_maps_to_low(
    source: str, service_name: str,
) -> None:
    """Any source string that does NOT match one of the four documented
    shapes must either raise an exception or map to ``low``.

    The design pins this as the "no other source string shapes are
    accepted" rule: the four-tier source-string contract must not drift
    silently. The current implementation raises ``ValueError`` for
    unrecognized inputs; a future relaxation that maps unknown shapes
    to ``low`` (so the resolver never crashes a user request on a
    novel source) is also acceptable. Both behaviors satisfy the
    property; behavior outside that set fails the test.
    """
    # Belt-and-braces: the strategy already filters to invalid shapes,
    # but Hypothesis' ``filter`` can be costly; ``assume`` lets a freak
    # draw be discarded silently rather than fail the test.
    assume(not _is_valid_shape(source))

    try:
        tier, prose = source_to_confidence(source, service_name)
    except Exception:
        # Any raised exception is acceptable for unrecognized shapes.
        return

    # If the call returned, it must have returned the ``low`` tier with
    # a non-empty prose string. ``medium-high`` / ``medium`` / ``high``
    # are reserved for the three well-formed non-default shapes.
    assert tier == "low", (
        f"unrecognized source {source!r} (service_name={service_name!r}) "
        f"must either raise or map to 'low'; got tier {tier!r}"
    )
    assert isinstance(prose, str) and prose.strip(), (
        f"unrecognized source {source!r}: returned tier 'low' but prose "
        f"is empty / non-string ({prose!r})"
    )
