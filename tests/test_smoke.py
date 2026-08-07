# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Smoke tests for the AWS DR Cost Estimator MCP tool methods.

Instantiates :class:`DRCostEstimatorTools` and exercises the three tool
methods directly (bypassing the FastMCP runtime) against a fake Cost
Explorer client. Asserts each call succeeds, returns non-empty payload
fields, and that the per-row totals add up to the reported aggregate
totals within rounding tolerance. Also validates the FastMCP tool
registration surface: exactly three tools, with the documented input
schemas.
"""

import asyncio
from pathlib import Path

from dr_cost_estimator_mcp.core.multipliers import STRATEGIES
from dr_cost_estimator_mcp.tools import DRCostEstimatorTools

from tests.test_aws_mode import _FakeCEClient

CENT = 0.01


def _run(coro):
    """Drive an ``async`` tool method to completion for a sync test."""
    return asyncio.run(coro)


def test_list_dr_strategies_smoke() -> None:
    tools = DRCostEstimatorTools()
    response = _run(tools.list_dr_strategies())

    assert response.success is True
    assert response.error is None
    # Four strategies, in canonical order.
    assert [s.id for s in response.strategies] == list(STRATEGIES)
    for strategy in response.strategies:
        assert strategy.label
        assert strategy.description


def test_analyze_dr_strategy_smoke() -> None:
    tools = DRCostEstimatorTools()
    response = _run(
        tools.analyze_dr_strategy(
            strategy="warm_standby",
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=_FakeCEClient(),
        )
    )

    assert response.success is True
    assert response.error is None
    assert response.strategy == "warm_standby"
    assert response.strategy_label
    assert len(response.estimates) > 0
    assert response.totals is not None

    # Per-row sums should match the reported totals. Each row is rounded
    # to the cent at the model boundary while the reported totals are
    # rounded once from unrounded floats, so the two paths can diverge
    # by up to ~half a cent per row.
    current_sum = sum(row.current_cost for row in response.estimates)
    dr_add_sum = sum(
        row.calculation.dr_additional_cost for row in response.estimates
    )
    total_sum = sum(row.calculation.total_with_dr for row in response.estimates)

    n = len(response.estimates)
    row_sum_tol = 0.005 * n + CENT
    assert abs(current_sum - response.totals.total_current_cost) <= row_sum_tol
    assert (
        abs(dr_add_sum - response.totals.total_dr_additional_cost) <= row_sum_tol
    )
    assert abs(total_sum - response.totals.total_with_dr) <= row_sum_tol

    # Totals are internally consistent: current + dr_add == total_with_dr.
    assert (
        abs(
            response.totals.total_current_cost
            + response.totals.total_dr_additional_cost
            - response.totals.total_with_dr
        )
        < CENT
    )

    # Confidence summary is populated and the dollar-weighted percentages
    # add up to (approximately) 100 when there is current cost. The four
    # tiers (`high`, `medium-high`, `medium`, `low`) reflect the
    # usage-type-group -> service -> category -> default resolver walk.
    assert response.confidence_summary is not None
    cs = response.confidence_summary
    if response.totals.total_current_cost > 0:
        assert (
            abs(
                cs.percent_cost_high_confidence
                + cs.percent_cost_medium_high_confidence
                + cs.percent_cost_medium_confidence
                + cs.percent_cost_low_confidence
                - 100.0
            )
            < 0.5
        )
    # Dollar buckets must sum to total_current_cost (within rounding).
    assert (
        abs(
            cs.total_cost_high_confidence
            + cs.total_cost_medium_high_confidence
            + cs.total_cost_medium_confidence
            + cs.total_cost_low_confidence
            - response.totals.total_current_cost
        )
        < CENT * len(response.estimates) + CENT
    )

    # Any service named in the default-fallback list must actually be a row
    # in the estimates with ``low`` confidence (the only tier that
    # populates ``services_using_default_fallback`` under the four-tier
    # model).
    low_services = {
        row.service for row in response.estimates if row.confidence == "low"
    }
    for name, _cost in cs.services_using_default_fallback:
        assert name in low_services

    # Per-account is always present.
    assert len(response.per_account) >= 1
    for acct in response.per_account:
        assert acct.account_id
        assert acct.account_name
        assert acct.totals is not None


def test_compare_dr_strategies_smoke() -> None:
    tools = DRCostEstimatorTools()
    response = _run(
        tools.compare_dr_strategies(
            months_back=2,
            linked_account_ids=None,
            profile=None,
            region=None,
            top_n=25,
            include_all_services=False,
            client=_FakeCEClient(),
        )
    )

    assert response.success is True
    assert response.error is None
    assert len(response.top_services) > 0
    assert response.per_strategy_totals

    # Each service has an entry for every strategy.
    for svc in response.top_services:
        assert set(svc.per_strategy.keys()) == set(STRATEGIES)

    # Per-strategy totals across services should match top-level totals
    # for each strategy. Per-row values are rounded at the model
    # boundary while the reported totals are rounded once from
    # unrounded floats, so the two paths can diverge by ~half a cent
    # per row.
    current_sum = sum(svc.current_cost for svc in response.top_services)
    n = len(response.top_services)
    row_sum_tol = 0.005 * n + CENT
    assert abs(current_sum - response.total_current_cost) <= row_sum_tol

    for sid in STRATEGIES:
        per_strategy = response.per_strategy_totals[sid]
        dr_add_sum = sum(
            svc.per_strategy[sid].dr_additional_cost
            for svc in response.top_services
        )
        total_sum = sum(
            svc.per_strategy[sid].total_with_dr for svc in response.top_services
        )
        assert (
            abs(dr_add_sum - per_strategy.total_dr_additional_cost) <= row_sum_tol
        )
        assert abs(total_sum - per_strategy.total_with_dr) <= row_sum_tol

    # Confidence summary is anchored on warm_standby and the dollar buckets
    # add up to total_current_cost within rounding tolerance.
    assert response.confidence_summary is not None
    cs = response.confidence_summary
    assert (
        abs(
            cs.total_cost_high_confidence
            + cs.total_cost_medium_high_confidence
            + cs.total_cost_medium_confidence
            + cs.total_cost_low_confidence
            - response.total_current_cost
        )
        < CENT * len(response.top_services) + CENT
    )

    # Per-account totals are always present.
    assert len(response.per_account_totals) >= 1
    for acct in response.per_account_totals:
        assert acct.account_id
        assert acct.account_name
        assert set(acct.per_strategy.keys()) == set(STRATEGIES)


# ---------------------------------------------------------------------------
# Smoke tests for the FastMCP tool registration surface.
# ---------------------------------------------------------------------------


def _list_tools_via_fastmcp() -> list:
    """Spin up a fresh FastMCP, register the tools, return the tool list.

    Used by the smoke tests below to introspect the live registration
    through the same code path the server uses at startup.
    """
    from fastmcp import FastMCP

    mcp = FastMCP("dr_cost_estimator_smoke_test")
    DRCostEstimatorTools().register(mcp)
    return _run(mcp.list_tools())


# Canonical names the server must register.
_EXPECTED_TOOL_NAMES = {
    "list_dr_strategies",
    "compare_dr_strategies",
    "analyze_dr_strategy",
}

# Legacy names that must not be registered after the CE-only refactor
# and the _from_aws suffix drop.
_FORBIDDEN_TOOL_NAMES = {
    "estimate_dr_costs",
    "estimate_dr_costs_from_aws",
    "compare_dr_strategies_from_aws",
    "analyze_dr_strategy_from_aws",
    "compare_dr_strategies_from_bill_estimate",
    "analyze_dr_strategy_from_bill_estimate",
    "create_dr_workload_estimate_from_aws",
}


def test_registered_tool_names_set() -> None:
    """The MCP server registers exactly the three canonical tools."""
    tools = _list_tools_via_fastmcp()
    names = {t.name for t in tools}

    assert names == _EXPECTED_TOOL_NAMES, (
        f"unexpected registered tool names: {sorted(names)} "
        f"(expected {sorted(_EXPECTED_TOOL_NAMES)})"
    )
    # Belt-and-braces: the legacy names must not slip back in via a
    # superset.
    assert names.isdisjoint(_FORBIDDEN_TOOL_NAMES)


# ---------------------------------------------------------------------------
# Per-tool input-schema expectations
# ---------------------------------------------------------------------------

# Required parameters (`Field(...)`) are not given a default and end up in
# the tool's ``required`` array; optional parameters appear under
# ``properties`` only.
_EXPECTED_TOOL_SCHEMAS: dict[str, dict] = {
    "list_dr_strategies": {
        "properties": set(),
        "required": set(),
    },
    "compare_dr_strategies": {
        "properties": {
            "months_back",
            "linked_account_ids",
            "profile",
            "region",
            "top_n",
            "include_all_services",
            "warm_standby_capacity",
            "tag_filters",
        },
        "required": set(),
    },
    "analyze_dr_strategy": {
        "properties": {
            "strategy",
            "months_back",
            "linked_account_ids",
            "profile",
            "region",
            "top_n",
            "include_all_services",
            "warm_standby_capacity",
            "tag_filters",
        },
        "required": {"strategy"},
    },
}


# Parameters that must never appear on any registered tool.
_FORBIDDEN_PARAMS = {
    "multi_account_mode",
    "include_pricing_prompt",
    "csv_content",
    "file_path",
    "prefer_engine_aware_multipliers",
    "use_usage_type_groups",
}


def test_tool_input_schemas() -> None:
    """Every registered tool's input schema matches the documented shape."""
    tools = {t.name: t for t in _list_tools_via_fastmcp()}

    for tool_name, expected in _EXPECTED_TOOL_SCHEMAS.items():
        assert tool_name in tools, f"{tool_name} not registered"
        schema = tools[tool_name].parameters
        properties = set(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))

        assert properties == expected["properties"], (
            f"{tool_name}: properties mismatch.\n"
            f"  got:      {sorted(properties)}\n"
            f"  expected: {sorted(expected['properties'])}"
        )
        assert required == expected["required"], (
            f"{tool_name}: required mismatch.\n"
            f"  got:      {sorted(required)}\n"
            f"  expected: {sorted(expected['required'])}"
        )

        # No tool may carry the deprecated parameters.
        forbidden_present = properties & _FORBIDDEN_PARAMS
        assert not forbidden_present, (
            f"{tool_name} exposes forbidden params: {sorted(forbidden_present)}"
        )


def test_response_models_have_no_pricing_prompt_field() -> None:
    """No Pydantic response model carries a ``pricing_validation_prompt`` field."""
    import inspect

    from pydantic import BaseModel

    from dr_cost_estimator_mcp import models as models_module

    offenders: list[str] = []
    for name, obj in inspect.getmembers(models_module):
        if not inspect.isclass(obj):
            continue
        if not issubclass(obj, BaseModel) or obj is BaseModel:
            continue
        if "pricing_validation_prompt" in obj.model_fields:
            offenders.append(name)

    assert not offenders, (
        "the following models still declare 'pricing_validation_prompt': "
        f"{offenders}"
    )


def test_prompt_generator_module_removed() -> None:
    """``dr_cost_estimator_mcp.core.prompt_generator`` no longer exists."""
    import importlib

    try:
        importlib.import_module("dr_cost_estimator_mcp.core.prompt_generator")
    except ImportError:
        return
    else:
        raise AssertionError(
            "dr_cost_estimator_mcp.core.prompt_generator was importable; "
            "the module must be deleted as part of the pricing-prompt removal."
        )


def test_no_test_imports_generate_pricing_prompt() -> None:
    """No file under ``tests/`` references the removed prompt-generator function."""
    tests_dir = Path(__file__).resolve().parent
    needles = ("generate_pricing_prompt", "prompt_generator")

    matches: list[tuple[str, int, str]] = []
    for path in tests_dir.rglob("*.py"):
        # Skip this file -- the literal strings above are part of the
        # test asserting the absence of the import, not an import
        # themselves.
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for needle in needles:
                if needle in line:
                    matches.append((str(path), lineno, line.strip()))

    assert not matches, (
        "found references to the removed prompt-generator in tests/:\n"
        + "\n".join(f"  {p}:{lineno}  {snippet}" for p, lineno, snippet in matches)
    )
