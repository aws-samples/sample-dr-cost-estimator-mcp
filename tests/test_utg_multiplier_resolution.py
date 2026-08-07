# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Property tests for usage-type-group multiplier resolution.

Tests verify that:
- Property 2: For any valid (service, group, strategy) triple in the table,
  resolve_usage_type_group_multiplier returns the exact multiplier and
  correct source.
- Property 3: For any supported service with an unknown group (including
  the synthetic remainder label), resolve_usage_type_group_multiplier
  returns None so the caller falls back to the blended service-level
  multiplier.
- Property 5: Override entries win on conflict and built-in entries are
  preserved after merge.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from dr_cost_estimator_mcp.core.multipliers import (
    STRATEGIES,
    SUPPORTED_UTG_SERVICES,
    USAGE_TYPE_GROUP_MULTIPLIERS,
    UTG_REMAINDER_GROUP,
    resolve_usage_type_group_multiplier,
)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# All valid (service, group, strategy) triples from the loaded table.
_VALID_TRIPLES: list[tuple[str, str, str]] = []
for _svc, _groups in USAGE_TYPE_GROUP_MULTIPLIERS.items():
    for _group, _strat_map in _groups.items():
        for _strategy in _strat_map:
            _VALID_TRIPLES.append((_svc, _group, _strategy))


def _valid_triple_strategy() -> st.SearchStrategy[tuple[str, str, str]]:
    """Draw from the set of all valid (service, group, strategy) triples
    present in USAGE_TYPE_GROUP_MULTIPLIERS.
    """
    assert _VALID_TRIPLES, (
        "USAGE_TYPE_GROUP_MULTIPLIERS is empty; cannot generate triples"
    )
    return st.sampled_from(_VALID_TRIPLES)


# All groups currently in the table, keyed by service.
_ALL_GROUPS_BY_SERVICE: dict[str, set[str]] = {
    _svc: set(_groups) for _svc, _groups in USAGE_TYPE_GROUP_MULTIPLIERS.items()
}

# All groups across all services (union) to use as an exclusion set.
_ALL_KNOWN_GROUPS: set[str] = set()
for _groups_set in _ALL_GROUPS_BY_SERVICE.values():
    _ALL_KNOWN_GROUPS.update(_groups_set)


# Alphabet for generating random unknown group names
_GROUP_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 -_:"
)


def _unknown_group_strategy() -> st.SearchStrategy[str]:
    """Generate a group string that is NOT in the multiplier table for any
    service. Uses a text strategy filtered against all known groups.
    """
    return st.text(
        alphabet=_GROUP_ALPHABET, min_size=1, max_size=40
    ).filter(lambda s: s.strip() not in _ALL_KNOWN_GROUPS and len(s.strip()) > 0)


def _supported_service_strategy() -> st.SearchStrategy[str]:
    """Draw from the set of supported UTG services."""
    assert SUPPORTED_UTG_SERVICES, (
        "SUPPORTED_UTG_SERVICES is empty; cannot generate service names"
    )
    return st.sampled_from(sorted(SUPPORTED_UTG_SERVICES))


def _strategy_strategy() -> st.SearchStrategy[str]:
    """Draw from the set of valid DR strategies."""
    return st.sampled_from(STRATEGIES)


# ---------------------------------------------------------------------------
# Property 2: Usage-type-group resolution correctness
# ---------------------------------------------------------------------------


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(triple=_valid_triple_strategy())
def test_utg_resolution_returns_correct_multiplier_and_source(
    triple: tuple[str, str, str],
) -> None:
    """For any valid (service, group, strategy) triple in the table,
    resolve_usage_type_group_multiplier returns the exact multiplier value
    and source "usage_type_group:<group>".

    The function is called with the verbatim CE-reported
    ``USAGE_TYPE_GROUP`` value (e.g. ``"RDS: Running Hours"``); there is
    no prefix stripping.
    """
    service, group, strategy = triple

    result = resolve_usage_type_group_multiplier(service, group, strategy)

    # Must not be None - this is a valid triple
    assert result is not None, (
        f"Expected a valid result for ({service!r}, {group!r}, {strategy!r}) "
        f"but got None"
    )

    multiplier, source = result

    # Verify exact multiplier value
    expected_multiplier = USAGE_TYPE_GROUP_MULTIPLIERS[service][group][strategy]
    assert multiplier == expected_multiplier, (
        f"For ({service!r}, {group!r}, {strategy!r}): "
        f"expected multiplier {expected_multiplier}, got {multiplier}"
    )

    # Verify source string format
    expected_source = f"usage_type_group:{group}"
    assert source == expected_source, (
        f"For ({service!r}, {group!r}, {strategy!r}): "
        f"expected source {expected_source!r}, got {source!r}"
    )


# ---------------------------------------------------------------------------
# Property 3: Usage-type-group fallback correctness
# ---------------------------------------------------------------------------


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    service=_supported_service_strategy(),
    unknown_group=_unknown_group_strategy(),
    strategy=_strategy_strategy(),
)
def test_utg_resolution_returns_none_for_unknown_group(
    service: str,
    unknown_group: str,
    strategy: str,
) -> None:
    """For any supported service with an unknown group (not in the table),
    resolve_usage_type_group_multiplier returns None, causing the caller
    to fall through to the service-level blended multiplier.
    """
    # Ensure the group is truly unknown for this specific service
    assume(unknown_group not in _ALL_GROUPS_BY_SERVICE.get(service, set()))

    result = resolve_usage_type_group_multiplier(service, unknown_group, strategy)

    assert result is None, (
        f"Expected None for ({service!r}, {unknown_group!r}, {strategy!r}), "
        f"but got {result!r}"
    )


def test_remainder_group_always_falls_back() -> None:
    """The synthetic remainder row must resolve via the blended multiplier
    for every supported service and strategy."""
    for service in sorted(SUPPORTED_UTG_SERVICES):
        for strategy in STRATEGIES:
            result = resolve_usage_type_group_multiplier(
                service, UTG_REMAINDER_GROUP, strategy
            )
            assert result is None, (
                f"{service} / {UTG_REMAINDER_GROUP} / {strategy} must fall "
                f"back to the blended multiplier, got {result!r}"
            )


# ---------------------------------------------------------------------------
# Property 5: Override merge semantics
# ---------------------------------------------------------------------------


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    override_service=_supported_service_strategy(),
    override_strategy=_strategy_strategy(),
    override_multiplier=st.floats(
        min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False
    ),
)
def test_override_merge_semantics(
    override_service: str,
    override_strategy: str,
    override_multiplier: float,
) -> None:
    """Verify that override entries win on conflict and built-in entries
    are preserved when DR_MULTIPLIERS_DIR is set with a partial override.

    Creates a temporary override directory with a partial
    usage_type_group_multipliers.json that overrides one entry. After
    re-loading, verifies:
    (a) The overridden entry has the override value.
    (b) Entries for services not named in the override are preserved
        (the merge is a shallow top-level dict update, so an override
        service entry replaces the whole built-in service entry).
    """
    # Pick the first group for the chosen service as the override target
    service_groups = sorted(USAGE_TYPE_GROUP_MULTIPLIERS[override_service].keys())
    assume(len(service_groups) > 0)
    override_group = service_groups[0]

    # Build a partial override that modifies only one entry
    override_data = {
        override_service: {
            override_group: {
                override_strategy: override_multiplier,
            }
        }
    }

    # Record a non-overridden entry (from a different service) to check
    # preservation across the merge.
    preserved_service = None
    preserved_group = None
    preserved_strategy = None
    preserved_value = None
    for svc in sorted(USAGE_TYPE_GROUP_MULTIPLIERS.keys()):
        if svc == override_service:
            continue
        for grp in sorted(USAGE_TYPE_GROUP_MULTIPLIERS[svc].keys()):
            for strat in STRATEGIES:
                if strat in USAGE_TYPE_GROUP_MULTIPLIERS[svc][grp]:
                    preserved_service = svc
                    preserved_group = grp
                    preserved_strategy = strat
                    preserved_value = USAGE_TYPE_GROUP_MULTIPLIERS[svc][grp][strat]
                    break
            if preserved_service:
                break
        if preserved_service:
            break

    assume(preserved_service is not None and preserved_value is not None)

    # Create temp directory with override file
    with tempfile.TemporaryDirectory() as tmpdir:
        override_path = Path(tmpdir) / "usage_type_group_multipliers.json"
        with open(override_path, "w", encoding="utf-8") as f:
            json.dump(override_data, f)

        # Temporarily set the override dir and reload
        old_env = os.environ.get("DR_MULTIPLIERS_DIR")
        os.environ["DR_MULTIPLIERS_DIR"] = tmpdir
        try:
            from dr_cost_estimator_mcp.core.multipliers import _load_json

            merged = _load_json("usage_type_group_multipliers.json")

            # (a) Override entry wins on conflict. `data.update(...)` is a
            # shallow top-level merge, so the override service entry
            # replaces the entire built-in service entry.
            assert override_service in merged
            assert merged[override_service] == override_data[override_service]

            # (b) Entries for services not in the override are preserved.
            assert preserved_service in merged, (
                f"Non-overridden service {preserved_service!r} was lost after merge"
            )
            actual_preserved = merged[preserved_service][preserved_group].get(
                preserved_strategy
            )
            assert actual_preserved == preserved_value, (
                f"Non-overridden value changed: expected {preserved_value}, "
                f"got {actual_preserved} for ({preserved_service!r}, "
                f"{preserved_group!r}, {preserved_strategy!r})"
            )
        finally:
            # Restore environment
            if old_env is None:
                os.environ.pop("DR_MULTIPLIERS_DIR", None)
            else:
                os.environ["DR_MULTIPLIERS_DIR"] = old_env
