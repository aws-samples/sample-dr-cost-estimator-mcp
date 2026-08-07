# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Drift guard: hand-authored tables in docs/methodology.md match the data.

Three tables in the methodology are written by hand and quote shipped
multiplier values:

* the Tier 1 curated-services table (a representative subset),
* the Tier 2 category table (warm-standby defaults),
* the usage-type-group tables (all values).

These tests parse each table and assert every quoted number equals the
value in the JSON data files, so refining a multiplier without updating
the methodology fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dr_cost_estimator_mcp.core.multipliers import (
    SERVICE_CATEGORIES,
    STRATEGIES,
    USAGE_TYPE_GROUP_MULTIPLIERS,
    get_multiplier,
)

_METHODOLOGY = (
    Path(__file__).resolve().parent.parent / "docs" / "methodology.md"
).read_text(encoding="utf-8")


def _parse_table_rows(section_heading: str) -> list[list[str]]:
    """Return the body rows (as cell lists) of the first markdown table
    after ``section_heading``."""
    start = _METHODOLOGY.index(section_heading)
    text = _METHODOLOGY[start:]
    rows: list[list[str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            in_table = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Skip the header separator row (---).
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            rows.append(cells)
        elif in_table:
            break
    assert rows, f"no table found after {section_heading!r}"
    return rows[1:]  # drop the header row


# ---------------------------------------------------------------------------
# Tier 1 curated table
# ---------------------------------------------------------------------------

# Doc display name -> service name to resolve through get_multiplier.
# (The doc uses friendly names; resolution goes through the same matching
# the tools use, so this also guards the alias wiring.)
_TIER1_NAME_OVERRIDES = {
    "Amazon RDS": "Amazon Relational Database Service",
    "EC2 Instances": "ec2-instances",
    "EC2-Other (EBS)": "EC2 - Other",
    "AWS Lambda": "lambda",
    "Amazon MSK": "managed streaming for apache kafka",
    "Amazon EFS": "elastic file system",
    "Amazon VPC": "vpc",
    "AWS KMS": "kms",
}


def test_tier1_table_matches_curated_data() -> None:
    rows = _parse_table_rows("### Tier 1: Curated")
    assert rows, "Tier 1 table is empty"
    mismatches: list[str] = []
    for cells in rows:
        doc_name = cells[0]
        service = _TIER1_NAME_OVERRIDES.get(doc_name, doc_name)
        doc_values = [float(c) for c in cells[1:5]]
        for strategy, doc_value in zip(STRATEGIES, doc_values):
            actual, source = get_multiplier(service, strategy)
            if source != "known":
                mismatches.append(
                    f"{doc_name}: resolves via {source!r}, not the curated tier"
                )
                break
            if actual != pytest.approx(doc_value):
                mismatches.append(
                    f"{doc_name} / {strategy}: doc says {doc_value}, "
                    f"data says {actual}"
                )
    assert not mismatches, (
        "methodology Tier 1 table out of sync with known_multipliers.json:\n"
        + "\n".join(f"  {m}" for m in mismatches)
    )


# ---------------------------------------------------------------------------
# Tier 2 category table (warm-standby column)
# ---------------------------------------------------------------------------

_CATEGORY_NAME_MAP = {
    "Compute (serverless)": "compute_serverless",
    "Compute (server/task)": "compute_server",
    "Data": "data",
    "Networking": "networking",
    "Streaming": "streaming",
    "Management": "management",
}


def test_tier2_category_table_matches_categories_data() -> None:
    rows = _parse_table_rows("### Tier 2: Category")
    assert len(rows) == len(SERVICE_CATEGORIES), (
        f"doc lists {len(rows)} categories, data has {len(SERVICE_CATEGORIES)}"
    )
    for cells in rows:
        doc_name, doc_ws = cells[0], float(cells[1])
        category = _CATEGORY_NAME_MAP[doc_name]
        actual = SERVICE_CATEGORIES[category]["defaults"]["warm_standby"]
        assert actual == pytest.approx(doc_ws), (
            f"category {doc_name}: doc says warm standby {doc_ws}, "
            f"data says {actual}"
        )


# ---------------------------------------------------------------------------
# Tier 3 default (warm standby figure quoted in prose)
# ---------------------------------------------------------------------------


def test_tier3_default_prose_matches_defaults_data() -> None:
    from dr_cost_estimator_mcp.core.multipliers import DEFAULT_MULTIPLIERS

    match = re.search(
        r"conservative fallback multiplier \(([\d.]+) for warm standby\)",
        _METHODOLOGY,
    )
    assert match, "Tier 3 prose sentence not found"
    assert float(match.group(1)) == pytest.approx(
        DEFAULT_MULTIPLIERS["warm_standby"]
    )


# ---------------------------------------------------------------------------
# Usage-type-group tables (all values, all supported services)
# ---------------------------------------------------------------------------


def test_utg_tables_match_utg_data() -> None:
    mismatches: list[str] = []
    doc_groups_seen: dict[str, set[str]] = {}
    for service, groups in USAGE_TYPE_GROUP_MULTIPLIERS.items():
        rows = _parse_table_rows(f"#### {service}")
        doc_groups_seen[service] = set()
        for cells in rows:
            group = cells[0]
            doc_groups_seen[service].add(group)
            if group not in groups:
                mismatches.append(
                    f"{service}: doc lists group {group!r} not in data"
                )
                continue
            doc_values = [float(c) for c in cells[1:5]]
            for strategy, doc_value in zip(STRATEGIES, doc_values):
                actual = groups[group][strategy]
                if actual != pytest.approx(doc_value):
                    mismatches.append(
                        f"{service} / {group} / {strategy}: doc says "
                        f"{doc_value}, data says {actual}"
                    )
        missing = set(groups) - doc_groups_seen[service]
        if missing:
            mismatches.append(f"{service}: doc missing groups {sorted(missing)}")
    assert not mismatches, (
        "methodology UTG tables out of sync with "
        "usage_type_group_multipliers.json:\n"
        + "\n".join(f"  {m}" for m in mismatches)
    )


def test_utg_supported_services_prose_matches_data() -> None:
    """The 'Supported Services and Groups' bullet list names exactly the
    services present in the data file."""
    section_start = _METHODOLOGY.index("### Supported Services and Groups")
    section = _METHODOLOGY[section_start : section_start + 1500]
    for service in USAGE_TYPE_GROUP_MULTIPLIERS:
        assert f"**{service}**" in section, (
            f"{service!r} missing from the supported-services list"
        )
