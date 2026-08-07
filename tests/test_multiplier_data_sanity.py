# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Data-sanity checks for the shipped multiplier tables.

These tests validate structural invariants of the curated data rather
than any specific value:

* Multipliers are non-decreasing across the strategy ladder
  (backup_restore <= pilot_light <= warm_standby <= active_active)
  unless the entry is on an explicit, documented exception list.
* Every entry carries all four strategies and an explanation per
  strategy.
* No orphaned "+0.05 data transfer adder" values remain. The flat 5%
  cross-region transfer surcharge was removed from the model (transfer
  is workload-dependent and documented as out of scope), so no curated
  multiplier may sit at x.05 above a whole tenth.
"""

import math

from dr_cost_estimator_mcp.core.multipliers import (
    KNOWN_EXPLANATIONS,
    KNOWN_MULTIPLIERS,
    SERVICE_CATEGORIES,
    STRATEGIES,
    USAGE_TYPE_GROUP_MULTIPLIERS,
)

# ---------------------------------------------------------------------------
# Monotonicity exceptions
# ---------------------------------------------------------------------------
# Strategies form a cost ladder: each step keeps more standing
# infrastructure in the DR region, so multipliers normally never
# decrease left-to-right. Consumption-billed services are the documented
# exception: at active/active, traffic SPLITS across regions instead of
# duplicating, so the additional cost drops back toward zero even though
# standby postures paid for provisioned capacity.
MONOTONICITY_EXCEPTIONS: dict[str, str] = {
    "kinesis": (
        "Provisioned shard-hours cost money in standby, but at "
        "active/active ingestion splits 50/50 so no additional charge."
    ),
    "kinesis firehose": "Same consumption-split shape as kinesis.",
    "kinesis analytics": "Same consumption-split shape as kinesis.",
    "dms": (
        "Warm standby keeps reduced-capacity replication instances "
        "running; at active/active instance sizes shrink in both regions "
        "and the workload splits, netting out to no additional charge."
    ),
}


def _ladder(mults: dict[str, float]) -> list[float]:
    return [mults[s] for s in STRATEGIES]


def test_known_multipliers_are_monotonic_unless_whitelisted() -> None:
    violations: list[str] = []
    for service, mults in KNOWN_MULTIPLIERS.items():
        if service in MONOTONICITY_EXCEPTIONS:
            continue
        ladder = _ladder(mults)
        for a, b in zip(ladder, ladder[1:]):
            if b < a:
                violations.append(f"{service}: {ladder}")
                break
    assert not violations, (
        "non-monotonic multiplier ladders without a documented exception:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_monotonicity_exceptions_are_actually_exceptions() -> None:
    """Every whitelisted service must really violate monotonicity.

    Keeps the whitelist honest: if a value change makes an entry
    monotonic again, the stale whitelist row must be removed.
    """
    stale: list[str] = []
    for service in MONOTONICITY_EXCEPTIONS:
        assert service in KNOWN_MULTIPLIERS, (
            f"whitelist names unknown service {service!r}"
        )
        ladder = _ladder(KNOWN_MULTIPLIERS[service])
        if all(b >= a for a, b in zip(ladder, ladder[1:])):
            stale.append(service)
    assert not stale, f"whitelisted but monotonic (remove from whitelist): {stale}"


def test_category_and_utg_multipliers_are_monotonic() -> None:
    for category, info in SERVICE_CATEGORIES.items():
        ladder = _ladder(info["defaults"])
        assert all(b >= a for a, b in zip(ladder, ladder[1:])), (
            f"category {category}: non-monotonic {ladder}"
        )
    for service, groups in USAGE_TYPE_GROUP_MULTIPLIERS.items():
        for group, mults in groups.items():
            ladder = _ladder(mults)
            assert all(b >= a for a, b in zip(ladder, ladder[1:])), (
                f"UTG {service} / {group}: non-monotonic {ladder}"
            )


def test_every_known_entry_has_all_strategies_and_explanations() -> None:
    for service, mults in KNOWN_MULTIPLIERS.items():
        assert set(mults.keys()) == set(STRATEGIES), (
            f"{service}: strategy keys {sorted(mults)} != {sorted(STRATEGIES)}"
        )
        explanations = KNOWN_EXPLANATIONS.get(service, {})
        missing = [s for s in STRATEGIES if not explanations.get(s)]
        assert not missing, f"{service}: missing explanations for {missing}"


def test_no_orphaned_transfer_adder_values_remain() -> None:
    """No multiplier may carry the removed +0.05 transfer adder.

    The historical flat "+5% for cross-region data transfer" surcharge
    was removed from the model. Any value ending in .05 above a whole
    tenth (1.05, 0.55 is fine -- the check is against X.X5 offsets of
    exactly +0.05 from a multiple of 0.1 at or above 1.0, where the
    adder was applied) indicates a leftover.
    """
    offenders: list[str] = []
    for service, mults in KNOWN_MULTIPLIERS.items():
        for strategy, value in mults.items():
            # The adder only ever produced values like 1.05 (base 1.0 +
            # 0.05). Flag values >= 1.0 that are 0.05 above a multiple
            # of 0.1.
            if value >= 1.0:
                remainder = round(math.fmod(round(value * 100), 10))
                if remainder == 5:
                    offenders.append(f"{service}.{strategy} = {value}")
    assert not offenders, (
        "orphaned +0.05 transfer-adder values remain:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# Copy-paste regression checks
# ---------------------------------------------------------------------------
# Historical bugs where an entry's explanations described a different
# service entirely (fsx carried EFS text; memorydb carried ElastiCache
# text; cloudwatch events carried CloudWatch logs/metrics text). Each
# entry below maps to tokens that must NOT appear in its explanations.
# Legitimate cross-references (e.g. ECS mentioning ECR for image
# billing) are not listed.
FOREIGN_SERVICE_TOKENS: dict[str, list[str]] = {
    "fsx": ["efs", "elastic file system"],
    "cloudwatch events": ["metrics", "alarms", "logs volume", "alerts"],
    "guardduty": ["data events split"],  # CloudTrail pricing language
}


def test_explanations_do_not_describe_a_different_service() -> None:
    violations: list[str] = []
    for service, forbidden in FOREIGN_SERVICE_TOKENS.items():
        explanations = KNOWN_EXPLANATIONS.get(service, {})
        for strategy, text in explanations.items():
            lowered = text.lower()
            for token in forbidden:
                if token in lowered:
                    violations.append(
                        f"{service}.{strategy} mentions {token!r}: {text}"
                    )
    assert not violations, (
        "explanations describing the wrong service:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_memorydb_entry_removed_and_resolves_via_data_category() -> None:
    """MemoryDB's curated entry was an unreviewed copy of ElastiCache
    analysis (it even said "ElastiCache data"); the honest resolution is
    the data-category fallback at medium confidence."""
    from dr_cost_estimator_mcp.core.multipliers import get_multiplier

    assert "memorydb" not in KNOWN_MULTIPLIERS

    multiplier, source = get_multiplier("Amazon MemoryDB", "warm_standby")
    assert source == "category:data"
    assert multiplier == SERVICE_CATEGORIES["data"]["defaults"]["warm_standby"]
