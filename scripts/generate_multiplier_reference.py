# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Generate ``docs/multiplier_reference.md`` from the multiplier data files.

The reference page is a human-readable rendering of the JSON data under
``dr_cost_estimator_mcp/core/data/`` - every curated service with its
per-strategy multipliers and rationales, the usage-type-group tables,
the category fallbacks, the portfolio default, and the skip list.

The page is GENERATED. Edit the JSON data files, then re-run:

    uv run python scripts/generate_multiplier_reference.py

``tests/test_multiplier_reference_sync.py`` regenerates the page and
diffs it against the checked-in file, so the reference can never drift
from the shipped data.

The generator reads the BUILT-IN data files directly (it ignores the
``DR_MULTIPLIERS_DIR`` override) so the output is deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _REPO_ROOT / "dr_cost_estimator_mcp" / "core" / "data"
_OUTPUT_PATH = _REPO_ROOT / "docs" / "multiplier_reference.md"

# Strategy order and column labels (kept in sync with
# dr_cost_estimator_mcp.core.multipliers.STRATEGIES / STRATEGY_LABELS,
# but defined locally so the generator depends only on the data files).
_STRATEGIES = ["backup_restore", "pilot_light", "warm_standby", "active_active"]
_STRATEGY_LABELS = {
    "backup_restore": "Backup & Restore",
    "pilot_light": "Pilot Light",
    "warm_standby": "Warm Standby",
    "active_active": "Active/Active",
}

# Words rendered with canonical capitalization in service headings.
_WORD_CASE = {
    "aws": "AWS",
    "amazon": "Amazon",
    "ec2": "EC2",
    "ec2-instances": "EC2-Instances",
    "ec2-other": "EC2-Other",
    "ec2-elb": "EC2-ELB",
    "ecs": "ECS",
    "eks": "EKS",
    "ecr": "ECR",
    "efs": "EFS",
    "emr": "EMR",
    "kms": "KMS",
    "msk": "MSK",
    "sns": "SNS",
    "sqs": "SQS",
    "vpc": "VPC",
    "dms": "DMS",
    "s3": "S3",
    "rds": "RDS",
    "fsx": "FSx",
    "elb": "ELB",
    "i/o": "I/O",
    "documentdb": "DocumentDB",
    "dynamodb": "DynamoDB",
    "elasticache": "ElastiCache",
    "cloudfront": "CloudFront",
    "cloudwatch": "CloudWatch",
    "guardduty": "GuardDuty",
    "opensearch": "OpenSearch",
    "mapreduce": "MapReduce",
    "(with": "(with",
    "mongodb": "MongoDB",
    "compatibility)": "compatibility)",
}


def _load(filename: str) -> dict | list:
    with open(_DATA_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def _display_name(key: str) -> str:
    """Render a lowercase data-file key as a readable heading."""
    words = key.split(" ")
    rendered = [
        _WORD_CASE.get(word, word.capitalize()) for word in words
    ]
    return " ".join(rendered)


def _fmt(value: float) -> str:
    """Format a multiplier: trim trailing zeros but keep at least one decimal."""
    text = f"{value:.3f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


def _dedupe_aliases(known: dict) -> list[tuple[str, list[str], dict]]:
    """Group curated entries whose values AND explanations are identical.

    Returns ``(canonical_key, all_keys, entry)`` tuples, ordered by
    canonical key. The canonical key is the longest alias (usually the
    full Cost Explorer display name).
    """
    by_signature: dict[str, list[str]] = {}
    entries_by_signature: dict[str, dict] = {}
    for key, entry in known.items():
        signature = json.dumps(entry, sort_keys=True)
        by_signature.setdefault(signature, []).append(key)
        entries_by_signature[signature] = entry

    groups: list[tuple[str, list[str], dict]] = []
    for signature, keys in by_signature.items():
        canonical = max(keys, key=len)
        groups.append((canonical, sorted(keys), entries_by_signature[signature]))
    groups.sort(key=lambda g: g[0])
    return groups


def generate() -> str:
    """Build the full markdown reference page as a string."""
    known: dict = _load("known_multipliers.json")
    categories: dict = _load("categories.json")
    defaults: dict = _load("defaults.json")
    skip: list = _load("skip_services.json")
    utg: dict = _load("usage_type_group_multipliers.json")

    lines: list[str] = []
    out = lines.append

    out("# DR Multiplier Reference")
    out("")
    out(
        "<!-- GENERATED FILE - do not edit by hand. -->"
    )
    out(
        "<!-- Edit the JSON files under dr_cost_estimator_mcp/core/data/ "
        "and re-run: uv run python scripts/generate_multiplier_reference.py -->"
    )
    out("")
    out(
        "Every multiplier the tools apply, with its rationale, rendered "
        "directly from the shipped data files. Multipliers express the "
        "**additional** monthly DR cost as a fraction of current "
        "primary-region spend for that line item. For how the numbers were "
        "derived and how to customize them, see "
        "[methodology.md](methodology.md)."
    )
    out("")
    out("Resolution order per line item: "
        "usage type group → curated service → category → default.")
    out("")

    # -- Usage-type-group tier ------------------------------------------------
    out("## Usage-Type-Group Multipliers (highest precedence)")
    out("")
    out(
        "Group keys are exact Cost Explorer `USAGE_TYPE_GROUP` dimension "
        "values. Spend in a supported service that no configured group "
        "captures resolves via the service's blended multiplier below. "
        "Per-group rationale lives in "
        "[methodology.md](methodology.md#usage-type-group-resolution)."
    )
    out("")
    for service in sorted(utg):
        out(f"### {service}")
        out("")
        header = "| Usage type group | " + " | ".join(
            _STRATEGY_LABELS[s] for s in _STRATEGIES
        ) + " |"
        out(header)
        out("|---|" + "---:|" * len(_STRATEGIES))
        for group, mults in utg[service].items():
            cells = " | ".join(_fmt(mults[s]) for s in _STRATEGIES)
            out(f"| {group} | {cells} |")
        out("")

    # -- Curated service tier -------------------------------------------------
    out("## Curated Service Multipliers")
    out("")
    out(
        "Entries are matched against the Cost Explorer service name "
        "(exact match first, then most-specific substring). Aliases "
        "listed under a heading share identical values and rationale."
    )
    out("")
    for canonical, keys, entry in _dedupe_aliases(known):
        out(f"### {_display_name(canonical)}")
        out("")
        aliases = ", ".join(f"`{k}`" for k in keys)
        out(f"Matches: {aliases}")
        out("")
        if entry.get("scales_with_warm_capacity"):
            out(
                "*Warm standby scales with the `warm_standby_capacity` tool "
                "parameter (value shown assumes the 50% default).*"
            )
            out("")
        out("| Strategy | Multiplier | Rationale |")
        out("|---|---:|---|")
        explanations = entry.get("explanations", {})
        for strategy in _STRATEGIES:
            value = entry.get(strategy)
            rationale = explanations.get(strategy, "")
            out(
                f"| {_STRATEGY_LABELS[strategy]} | {_fmt(value)} | {rationale} |"
            )
        out("")

    # -- Category tier ----------------------------------------------------------
    out("## Category Fallbacks")
    out("")
    out(
        "Services without a curated entry match a category by keyword and "
        "use the category defaults."
    )
    out("")
    header = "| Category | " + " | ".join(
        _STRATEGY_LABELS[s] for s in _STRATEGIES
    ) + " | Keywords | Rationale |"
    out(header)
    out("|---|" + "---:|" * len(_STRATEGIES) + "---|---|")
    for category, info in categories.items():
        cells = " | ".join(_fmt(info["defaults"][s]) for s in _STRATEGIES)
        keywords = ", ".join(info["keywords"])
        out(f"| {category} | {cells} | {keywords} | {info['rationale']} |")
    out("")

    # -- Default tier -----------------------------------------------------------
    out("## Portfolio Default (last resort)")
    out("")
    header = "| " + " | ".join(
        _STRATEGY_LABELS[s] for s in _STRATEGIES
    ) + " | Rationale |"
    out(header)
    out("|" + "---:|" * len(_STRATEGIES) + "---|")
    cells = " | ".join(_fmt(defaults[s]) for s in _STRATEGIES)
    out(f"| {cells} | {defaults.get('rationale', '')} |")
    out("")

    # -- Skip list ----------------------------------------------------------------
    out("## Excluded Line Items (skip list)")
    out("")
    out(
        "Service names containing any of these substrings are excluded "
        "from DR estimates entirely:"
    )
    out("")
    out(", ".join(f"`{s}`" for s in skip))
    out("")

    return "\n".join(lines)


def main() -> None:
    _OUTPUT_PATH.write_text(generate(), encoding="utf-8")
    print(f"wrote {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
