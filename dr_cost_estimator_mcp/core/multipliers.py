# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
AWS DR Cost Multipliers & Service Classification

This module contains the known DR cost multipliers for AWS services,
category-based defaults for unknown services, and explanatory comments
for each multiplier.

Multipliers represent the ADDITIONAL cost as a fraction of current spend
in the DR region. e.g. 0.8 means DR adds 80% of current monthly spend.

Data is loaded from JSON files in the ``data/`` directory at import time.
Set the ``DR_MULTIPLIERS_DIR`` environment variable to a directory path
to override or extend the built-in data files. If the override directory
contains a ``known_multipliers.json`` or ``usage_type_group_multipliers.json``,
its entries are merged on top of the built-in ones (override entries win
on key conflict).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

_BUILTIN_DATA_DIR = Path(__file__).resolve().parent / "data"


_MERGE_ON_OVERRIDE_FILES = frozenset(
    {"known_multipliers.json", "usage_type_group_multipliers.json"}
)


def _load_json(filename: str) -> dict | list:
    """Load a JSON file from the override dir (if set) or built-in data dir.

    For files in ``_MERGE_ON_OVERRIDE_FILES``, the override is MERGED on
    top of the built-in data (override entries win on key conflict).
    For all other files, the override completely replaces the built-in file.
    """
    override_dir_str = os.environ.get("DR_MULTIPLIERS_DIR")
    override_dir = Path(override_dir_str) if override_dir_str else None

    builtin_path = _BUILTIN_DATA_DIR / filename
    override_path = override_dir / filename if override_dir else None

    if filename in _MERGE_ON_OVERRIDE_FILES:
        # Merge strategy: load built-in first, then overlay override entries.
        with open(builtin_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if override_path and override_path.is_file():
            with open(override_path, "r", encoding="utf-8") as f:
                override_data = json.load(f)
            data.update(override_data)
        return data
    else:
        # For non-merge files: prefer override if it exists, else built-in.
        if override_path and override_path.is_file():
            target = override_path
        else:
            target = builtin_path
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# Load data from JSON files
# ---------------------------------------------------------------------------

_known_data: dict = _load_json("known_multipliers.json")
_categories_data: dict = _load_json("categories.json")
_skip_data: list = _load_json("skip_services.json")
_defaults_data: dict = _load_json("defaults.json")
_utg_data: dict = _load_json("usage_type_group_multipliers.json")


# ---------------------------------------------------------------------------
# Reconstruct public data structures from loaded JSON
# ---------------------------------------------------------------------------

# KNOWN_MULTIPLIERS: normalized service name -> { strategy -> multiplier }
KNOWN_MULTIPLIERS: dict[str, dict[str, float]] = {}
# Keys present in a service entry that are NOT per-strategy multiplier values.
# Filtering on this set lets us add new metadata fields to
# ``known_multipliers.json`` without polluting the strategy-keyed dicts
# that the rest of the codebase reads as ``mults.get(strategy)``.
_NON_MULTIPLIER_KEYS = frozenset({"explanations", "scales_with_warm_capacity"})
for _svc, _entry in _known_data.items():
    KNOWN_MULTIPLIERS[_svc] = {
        k: v for k, v in _entry.items() if k not in _NON_MULTIPLIER_KEYS
    }

# KNOWN_WARM_SCALING: curated entries whose warm-standby multiplier
# represents fleet-shaped compute capacity and therefore scales linearly
# with the caller-supplied ``warm_standby_capacity``. Entries without
# the flag (fixed control-plane fees, replica-count-driven databases,
# storage replication) ignore the parameter.
KNOWN_WARM_SCALING: set[str] = {
    _svc
    for _svc, _entry in _known_data.items()
    if _entry.get("scales_with_warm_capacity")
}

# KNOWN_EXPLANATIONS: normalized service name -> { strategy -> explanation }
KNOWN_EXPLANATIONS: dict[str, dict[str, str]] = {}
for _svc, _entry in _known_data.items():
    if "explanations" in _entry:
        KNOWN_EXPLANATIONS[_svc] = _entry["explanations"]

# SERVICE_CATEGORIES
SERVICE_CATEGORIES: dict[str, dict] = _categories_data

# SKIP_SERVICES
SKIP_SERVICES: set[str] = set(_skip_data)

# DEFAULT_MULTIPLIERS
DEFAULT_MULTIPLIERS: dict[str, float] = {
    k: v for k, v in _defaults_data.items() if k != "rationale"
}
DEFAULT_RATIONALE: str = _defaults_data.get(
    "rationale", "No specific guidance available. Using conservative general estimate."
)

# USAGE_TYPE_GROUP_MULTIPLIERS:
#   service_display_name -> { ce_usage_type_group -> { strategy -> multiplier } }
#
# Keys at the second level are EXACT Cost Explorer ``USAGE_TYPE_GROUP``
# dimension values (e.g. ``"RDS: Running Hours"``,
# ``"EC2: ELB - LCU Running Hours"``), verified against the live API.
# The fetcher issues one filtered GetCostAndUsage query per configured
# group (the API does not support USAGE_TYPE_GROUP as a GroupBy
# dimension, only as a Filter), so every key here costs one extra API
# call when the service has spend.
USAGE_TYPE_GROUP_MULTIPLIERS: dict[str, dict[str, dict[str, float]]] = _utg_data

# SUPPORTED_UTG_SERVICES: set of service display names that have UTG multiplier entries
SUPPORTED_UTG_SERVICES: set[str] = set(_utg_data.keys())

#: Synthetic group label for spend inside a supported service that did
#: not match any configured usage type group. Rows carrying this label
#: resolve via the blended service-level multiplier.
UTG_REMAINDER_GROUP = "(other usage)"


def utg_groups_for_service(service_display_name: str) -> list[str]:
    """Return the configured CE usage-type-group values for a service.

    Empty list when the service has no usage-type-group entries.
    """
    return list(USAGE_TYPE_GROUP_MULTIPLIERS.get(service_display_name, {}))


# ---------------------------------------------------------------------------
# Strategy constants (not user-extensible data)
# ---------------------------------------------------------------------------

STRATEGIES = ["backup_restore", "pilot_light", "warm_standby", "active_active"]
STRATEGY_LABELS = {
    "backup_restore": "Backup & Restore",
    "pilot_light": "Pilot Light",
    "warm_standby": "Warm Standby",
    "active_active": "Active/Active",
}

# One-sentence descriptions per strategy, lifted from the CLI docstring.
STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "backup_restore": (
        "Lowest-cost DR strategy and highest RTO/RPO. Only backups are "
        "replicated to the DR region; compute, databases, and networking "
        "are re-provisioned from backup on failover. Best when hours of "
        "downtime and data loss are acceptable."
    ),
    "pilot_light": (
        "Core data services (databases, storage) are replicated to the DR "
        "region and kept running, but compute is scaled to zero. On "
        "failover, compute is scaled up against the already-replicated "
        "data. Moderate RTO (tens of minutes), near-zero RPO for data."
    ),
    "warm_standby": (
        "A functional but scaled-down copy of production runs continuously "
        "in the DR region. Failover scales compute up to full capacity "
        "against already-warm services. Low RTO (minutes), near-zero RPO. "
        "Higher steady-state cost than pilot light."
    ),
    "active_active": (
        "Full production capacity runs in both regions simultaneously, "
        "with traffic load-balanced across them. Failover is effectively "
        "instantaneous. Highest steady-state cost, but for data services "
        "with native multi-region replication (for example DynamoDB "
        "global tables, S3 CRR) the marginal cost can be lower than "
        "warm standby because there is no idle standby capacity."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_service_name(name: str) -> str:
    """Lowercase and strip whitespace for matching."""
    return name.strip().lower()


def _match_known_key(name: str) -> str | None:
    """Return the ``KNOWN_MULTIPLIERS`` key matching ``name``, or ``None``.

    Resolution is most-specific-first:

    1. An exact match on the normalized name always wins.
    2. Otherwise, among all keys that substring-match (``key in name`` or
       ``name in key``), the LONGEST key wins.

    The longest-key rule matters because curated keys can be substrings
    of each other. Cost Explorer reports EKS as "Amazon Elastic Container
    Service for Kubernetes", which substring-matches both the
    ``elastic container service`` (ECS) key and the
    ``elastic container service for kubernetes`` (EKS) key; picking the
    longest candidate resolves it to the EKS entry instead of whichever
    happens to appear first in the data file.

    Substring matches must align on word boundaries: the key
    ``"amazon q"`` matches ``"amazon q"`` and ``"amazon q business"``
    but NOT ``"amazon quicksight"``, where the key ends mid-word.
    Without this rule, short keys silently capture unrelated services.

    ``name`` must already be normalized via :func:`normalize_service_name`.
    """
    if name in KNOWN_MULTIPLIERS:
        return name
    candidates = [
        key
        for key in KNOWN_MULTIPLIERS
        if _word_bounded_in(key, name) or _word_bounded_in(name, key)
    ]
    if not candidates:
        return None
    return max(candidates, key=len)


def _word_bounded_in(needle: str, haystack: str) -> bool:
    """True when ``needle`` occurs in ``haystack`` aligned on word boundaries.

    A boundary is the string edge or any non-alphanumeric character, so
    hyphenated keys like ``"ec2-elb"`` and spaced keys like
    ``"amazon q"`` both work. Mid-word hits (``"amazon q"`` inside
    ``"amazon quicksight"``) are rejected.
    """
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index == -1:
            return False
        before_ok = index == 0 or not haystack[index - 1].isalnum()
        end = index + len(needle)
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = index + 1


def should_skip_service(name: str) -> bool:
    """Check if a service should be excluded from DR estimates."""
    lower = name.lower()
    return any(skip in lower for skip in SKIP_SERVICES)


def classify_service(service_name: str) -> str:
    """Return the category name for a service based on keyword matching."""
    name = normalize_service_name(service_name)
    for category, info in SERVICE_CATEGORIES.items():
        for keyword in info["keywords"]:
            if keyword in name:
                return category
    return "unknown"


#: The warm-standby compute-capacity assumption baked into the shipped
#: multipliers (~50% of primary, the Well-Architected midpoint). The
#: ``warm_standby_capacity`` tool parameter rescales flagged entries
#: relative to this baseline.
DEFAULT_WARM_CAPACITY = 0.5


def _scale_warm(base: float, warm_standby_capacity: float) -> float:
    """Rescale a warm-standby multiplier to a different warm capacity.

    The shipped values assume :data:`DEFAULT_WARM_CAPACITY`; a caller
    running a 25% warm fleet gets ``base * (0.25 / 0.5)``.
    """
    return base * (warm_standby_capacity / DEFAULT_WARM_CAPACITY)


def get_multiplier(
    service_name: str,
    strategy: str,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> tuple[float, str]:
    """Return ``(multiplier, source)`` for a service + strategy.

    Walks the three resolution tiers in order:

    1. Service tier: match against curated ``KNOWN_MULTIPLIERS`` entries
       via :func:`_match_known_key` (exact match first, then the longest
       substring-matching key). Source: ``"known"``.
    2. Category tier: keyword-based classification via
       :func:`classify_service`. Source: ``"category:<name>"``.
    3. Default tier: conservative portfolio-wide fallback.
       Source: ``"default"``.

    ``warm_standby_capacity`` (0.1-1.0, default 0.5) rescales the
    warm-standby multiplier for entries flagged
    ``scales_with_warm_capacity`` - fleet-shaped compute whose warm
    posture is "run X% of the primary fleet". All other entries, all
    other strategies, and the default tier ignore the parameter.
    """
    name = normalize_service_name(service_name)

    # 1. Service tier.
    known_key = _match_known_key(name)
    if known_key is not None:
        mults = KNOWN_MULTIPLIERS[known_key]
        value = mults.get(strategy, DEFAULT_MULTIPLIERS[strategy])
        if strategy == "warm_standby" and known_key in KNOWN_WARM_SCALING:
            value = _scale_warm(value, warm_standby_capacity)
        return value, "known"

    # 2. Category tier.
    category = classify_service(service_name)
    if category != "unknown":
        info = SERVICE_CATEGORIES[category]
        value = info["defaults"][strategy]
        if strategy == "warm_standby" and info.get("scales_with_warm_capacity"):
            value = _scale_warm(value, warm_standby_capacity)
        return value, f"category:{category}"

    # 3. Default tier.
    return DEFAULT_MULTIPLIERS[strategy], "default"


def resolve_usage_type_group_multiplier(
    service_display_name: str,
    usage_type_group: str,
    strategy: str,
) -> tuple[float, str] | None:
    """Return (multiplier, source) if a UTG entry matches, else None.

    ``usage_type_group`` is the raw CE-reported ``USAGE_TYPE_GROUP``
    dimension value (e.g. ``"RDS: Running Hours"``), looked up verbatim
    in ``USAGE_TYPE_GROUP_MULTIPLIERS[service_display_name]``.

    Source string format: ``"usage_type_group:<group>"``
    (e.g. ``"usage_type_group:RDS: Running Hours"``).

    Returns ``None`` when no match is found (including for the synthetic
    :data:`UTG_REMAINDER_GROUP` label) so the caller falls through to
    the service-level blended multiplier.
    """
    service_entry = USAGE_TYPE_GROUP_MULTIPLIERS.get(service_display_name)
    if service_entry is None:
        return None

    group_entry = service_entry.get(usage_type_group)
    if group_entry is None:
        return None

    multiplier = group_entry.get(strategy)
    if multiplier is None:
        return None

    return (multiplier, f"usage_type_group:{usage_type_group}")


def get_explanation(service_name: str, strategy: str) -> str:
    """Return the explanation for a service's DR multiplier, if available.

    Uses the same :func:`_match_known_key` resolution as
    :func:`get_multiplier` so the explanation always describes the entry
    whose multiplier was actually applied.
    """
    name = normalize_service_name(service_name)

    known_key = _match_known_key(name)
    if known_key is not None:
        return KNOWN_EXPLANATIONS.get(known_key, {}).get(strategy, "")

    category = classify_service(service_name)
    if category != "unknown":
        return SERVICE_CATEGORIES[category].get("rationale", DEFAULT_RATIONALE)

    return DEFAULT_RATIONALE


def get_rationale(
    service_name: str,
    strategy: str,
    warm_standby_capacity: float = DEFAULT_WARM_CAPACITY,
) -> str:
    """Return a human-readable rationale for the (service, strategy) pair.

    Resolution order mirrors :func:`get_multiplier` exactly:

    1. KNOWN_MULTIPLIERS / KNOWN_EXPLANATIONS hit (same
       :func:`_match_known_key` resolution as ``get_multiplier``):

       - If a per-strategy explanation is on file, return it verbatim.
       - If the service matches a curated entry but the strategy key is missing,
         return ``"Curated multiplier for <service_name>; no per-strategy
         rationale on file."``.
    2. SERVICE_CATEGORIES hit via :func:`classify_service`:
       return ``"<category> category fallback: <category rationale>"``.
    3. Default fallback: ``"Default fallback: <DEFAULT_RATIONALE>"``.

    The ``<service_name>`` placeholder uses the original (un-normalized) name
    as supplied by the caller, so the rationale reads naturally even when the
    canonical key in ``known_multipliers.json`` is a short alias such as
    ``"ec2"``.

    When ``warm_standby_capacity`` differs from the shipped 50% baseline
    and the entry scales with warm capacity, a suffix is appended so the
    prose stays truthful for the scaled number.

    The function never returns an empty string and never includes the numeric
    multiplier value.
    """
    name = normalize_service_name(service_name)

    def _warm_suffix(scales: bool) -> str:
        if (
            scales
            and strategy == "warm_standby"
            and warm_standby_capacity != DEFAULT_WARM_CAPACITY
        ):
            return (
                f" Warm capacity scaled to {warm_standby_capacity:.0%} of "
                "primary per the warm_standby_capacity parameter "
                "(shipped baseline is 50%)."
            )
        return ""

    # 1. Curated KNOWN_MULTIPLIERS hit. Use the same matching resolution
    # as get_multiplier so the two functions can never disagree.
    known_key = _match_known_key(name)
    if known_key is not None:
        suffix = _warm_suffix(known_key in KNOWN_WARM_SCALING)
        explanations = KNOWN_EXPLANATIONS.get(known_key, {})
        per_strategy = explanations.get(strategy)
        if per_strategy:
            return per_strategy + suffix
        return (
            f"Curated multiplier for {service_name}; "
            "no per-strategy rationale on file." + suffix
        )

    # 2. Category fallback.
    category = classify_service(service_name)
    if category != "unknown":
        info = SERVICE_CATEGORIES[category]
        suffix = _warm_suffix(bool(info.get("scales_with_warm_capacity")))
        category_rationale = info.get("rationale", DEFAULT_RATIONALE)
        return f"{category} category fallback: {category_rationale}" + suffix

    # 3. Default fallback.
    return f"Default fallback: {DEFAULT_RATIONALE}"
