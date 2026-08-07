# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Drift guard: docs/multiplier_reference.md must match the data files.

The reference page is generated from the JSON data under
``dr_cost_estimator_mcp/core/data/``. This test regenerates it in memory
and diffs against the checked-in file, so any data change that is not
accompanied by a regeneration (or any hand edit to the generated page)
fails CI with instructions.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_multiplier_reference.py"
_REFERENCE_PATH = _REPO_ROOT / "docs" / "multiplier_reference.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_multiplier_reference", _GENERATOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_multiplier_reference_is_in_sync_with_data() -> None:
    generator = _load_generator()
    expected = generator.generate()
    actual = _REFERENCE_PATH.read_text(encoding="utf-8")

    assert actual == expected, (
        "docs/multiplier_reference.md is out of sync with the data files.\n"
        "Regenerate it with:\n"
        "    uv run python scripts/generate_multiplier_reference.py\n"
        "(The page is generated - edit the JSON data files, not the page.)"
    )


def test_multiplier_reference_carries_generated_marker() -> None:
    text = _REFERENCE_PATH.read_text(encoding="utf-8")
    assert "GENERATED FILE - do not edit by hand" in text
