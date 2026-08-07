# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for live Cost Explorer service-name resolution.

Cost Explorer's ``GroupBy=SERVICE`` dimension reports EBS and related
charges under the display name ``"EC2 - Other"`` (with spaces). The
curated table originally only carried the key ``"ec2-other"``, which the
substring-matching predicate in :func:`get_multiplier` can never match
against the live name, so the row silently fell through to the
``compute_server`` category tier: same multiplier values, but the
curated rationale and ``medium-high`` confidence never surfaced. These
tests pin the live CE display name to the curated tier.
"""

from dr_cost_estimator_mcp.core.multipliers import (
    STRATEGIES,
    get_multiplier,
    get_rationale,
)

# The exact display name reported by the Cost Explorer SERVICE dimension.
LIVE_CE_NAME = "EC2 - Other"

EXPECTED_MULTIPLIERS = {
    "backup_restore": 0.0,
    "pilot_light": 0.0,
    "warm_standby": 0.5,
    "active_active": 1.0,
}


def test_ec2_other_live_name_resolves_at_curated_tier() -> None:
    for strategy in STRATEGIES:
        multiplier, source = get_multiplier(LIVE_CE_NAME, strategy)
        assert source == "known", (
            f"{LIVE_CE_NAME!r} fell through to {source!r} for {strategy};"
            " expected the curated tier"
        )
        assert multiplier == EXPECTED_MULTIPLIERS[strategy]


def test_ec2_other_live_name_gets_curated_rationale() -> None:
    for strategy in STRATEGIES:
        rationale = get_rationale(LIVE_CE_NAME, strategy)
        assert "category fallback" not in rationale
        assert "Default fallback" not in rationale
        # The curated ec2-other explanations all talk about EBS.
        assert "EBS" in rationale


# ---------------------------------------------------------------------------
# EKS vs ECS shadowing (most-specific-match resolution)
# ---------------------------------------------------------------------------

# Cost Explorer has reported EKS under both display names over time.
EKS_LIVE_CE_NAMES = [
    "Amazon Elastic Container Service for Kubernetes",
    "Amazon Elastic Kubernetes Service",
]

# The EKS service line in Cost Explorer is essentially the control plane
# fee (worker nodes bill under EC2), so pilot light and above run the
# control plane in DR at full (flat) cost.
EKS_EXPECTED_MULTIPLIERS = {
    "backup_restore": 0.0,
    "pilot_light": 1.0,
    "warm_standby": 1.0,
    "active_active": 1.0,
}

ECS_LIVE_CE_NAME = "Amazon Elastic Container Service"

ECS_EXPECTED_MULTIPLIERS = {
    "backup_restore": 0.0,
    "pilot_light": 0.0,
    "warm_standby": 0.5,
    "active_active": 1.0,
}


def test_eks_live_names_resolve_to_eks_not_ecs() -> None:
    """EKS CE display names must resolve to the EKS control-plane entry.

    Regression: "Amazon Elastic Container Service for Kubernetes"
    substring-matches the shorter ECS key ("elastic container service"),
    and dict-order matching used to hand EKS spend the ECS multipliers
    (pilot light 0.0 instead of 1.0). Most-specific-match resolution must
    pick the longest matching key.
    """
    for live_name in EKS_LIVE_CE_NAMES:
        for strategy in STRATEGIES:
            multiplier, source = get_multiplier(live_name, strategy)
            assert source == "known", (
                f"{live_name!r} fell through to {source!r} for {strategy}"
            )
            assert multiplier == EKS_EXPECTED_MULTIPLIERS[strategy], (
                f"{live_name!r} {strategy}: got {multiplier}, "
                f"expected {EKS_EXPECTED_MULTIPLIERS[strategy]} "
                "(did it resolve to the ECS entry?)"
            )


def test_eks_live_names_get_control_plane_rationale() -> None:
    for live_name in EKS_LIVE_CE_NAMES:
        for strategy in STRATEGIES:
            rationale = get_rationale(live_name, strategy)
            assert "category fallback" not in rationale
            assert "Default fallback" not in rationale
            # All EKS explanations talk about the control plane / cluster,
            # never about ECS tasks.
            assert "task" not in rationale.lower()


def test_ecs_live_name_still_resolves_to_ecs() -> None:
    """The most-specific-match change must not disturb plain ECS names."""
    for strategy in STRATEGIES:
        multiplier, source = get_multiplier(ECS_LIVE_CE_NAME, strategy)
        assert source == "known"
        assert multiplier == ECS_EXPECTED_MULTIPLIERS[strategy]


def test_exact_key_beats_longer_substring_candidates() -> None:
    """An exact key match wins even when longer keys also substring-match."""
    # "ecs" is an exact curated key; "elastic container service" is a
    # longer key but does not substring-match the literal input "ecs".
    multiplier, source = get_multiplier("ecs", "warm_standby")
    assert source == "known"
    assert multiplier == 0.5


# ---------------------------------------------------------------------------
# Word-boundary matching (short-key shadowing)
# ---------------------------------------------------------------------------


def test_amazon_q_resolves_to_curated_zero() -> None:
    for strategy in STRATEGIES:
        multiplier, source = get_multiplier("Amazon Q", strategy)
        assert source == "known"
        assert multiplier == 0.0


def test_codewhisperer_legacy_line_resolves_to_curated_zero() -> None:
    for strategy in STRATEGIES:
        multiplier, source = get_multiplier("Amazon CodeWhisperer", strategy)
        assert source == "known"
        assert multiplier == 0.0


def test_quicksight_not_captured_by_amazon_q_key() -> None:
    """Regression: the 'amazon q' key must not substring-match QuickSight.

    Raw substring matching would let 'amazon q' capture 'amazon
    quicksight' (a mid-word hit) and silently zero QuickSight's DR
    estimate. Word-boundary matching keeps QuickSight on the default
    tier, where it is deliberately left as a flagged judgment call.
    """
    multiplier, source = get_multiplier("Amazon QuickSight", "warm_standby")
    assert source == "default", (
        f"QuickSight resolved via {source!r}; the 'amazon q' key is "
        "shadowing it (word-boundary matching broken?)"
    )
    assert multiplier != 0.0


def test_qldb_not_captured_by_amazon_q_key() -> None:
    """Same mid-word hazard as QuickSight, via the ledger keyword tier."""
    multiplier, source = get_multiplier("Amazon QLDB", "warm_standby")
    assert source != "known", (
        f"QLDB resolved via {source!r}; expected a category/default tier, "
        "not the 'amazon q' curated entry"
    )


def test_hyphen_and_space_boundaries_still_match() -> None:
    """Boundary matching must treat hyphens and spaces as boundaries so
    existing keys keep resolving."""
    cases = {
        "EC2 - Other": 0.5,          # spaced alias
        "EC2-Instances": 0.5,        # hyphenated key, exact
        "Amazon Elastic Load Balancing": 0.2,  # multi-word substring
    }
    for name, expected_ws in cases.items():
        multiplier, source = get_multiplier(name, "warm_standby")
        assert source == "known", f"{name!r} fell to {source!r}"
        assert multiplier == expected_ws, name
