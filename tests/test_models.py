# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the rewritten Pydantic response models.

Covers:

* :class:`ConfidenceSummary` field types and ``services_using_default_fallback``
  default / populated behaviour.
* :class:`AnalyzeRow` ``confidence`` literal validation and the
  :class:`Calculation` field accepting both an instance and a dict.
* :class:`ErrorInfo` ``next_steps`` default-empty-list behaviour and
  arbitrary-string acceptance.
* Removed-models guard: the legacy names (``EstimateResponse``,
  ``MultiAccountEstimateResponse``, ``MultiplierConfidence``) and the
  legacy ``pricing_validation_prompt`` field are NOT importable /
  reachable from :mod:`dr_cost_estimator_mcp.models`.
"""

from __future__ import annotations

import importlib
import json

import pytest
from pydantic import ValidationError

from dr_cost_estimator_mcp.models import (
    AnalyzeDrStrategyResponse,
    AnalyzeRow,
    Calculation,
    CompareDrStrategiesResponse,
    ConfidenceSummary,
    ErrorInfo,
)


# ---------------------------------------------------------------------------
# ConfidenceSummary
# ---------------------------------------------------------------------------


def _confidence_summary_kwargs(**overrides: object) -> dict:
    """Build a minimal kwargs dict for :class:`ConfidenceSummary`.

    All seven required numeric fields are provided so individual tests
    can override exactly the field they care about.
    """
    base: dict = {
        "total_cost_high_confidence": 100.0,
        "total_cost_medium_confidence": 50.0,
        "total_cost_low_confidence": 25.0,
        "percent_cost_high_confidence": 57.14,
        "percent_cost_medium_confidence": 28.57,
        "percent_cost_low_confidence": 14.29,
    }
    base.update(overrides)
    return base


def test_confidence_summary_accepts_valid_floats_and_default_fallback_list() -> None:
    """All seven required floats accepted; ``services_using_default_fallback`` defaults to []."""
    summary = ConfidenceSummary(**_confidence_summary_kwargs())

    assert summary.total_cost_high_confidence == 100.0
    assert summary.total_cost_medium_confidence == 50.0
    assert summary.total_cost_low_confidence == 25.0
    assert summary.percent_cost_high_confidence == 57.14
    assert summary.percent_cost_medium_confidence == 28.57
    assert summary.percent_cost_low_confidence == 14.29
    # Empty default factory: no caller need supply the list.
    assert summary.services_using_default_fallback == []


def test_confidence_summary_accepts_populated_default_fallback_list() -> None:
    """``services_using_default_fallback`` accepts ``(name, cost)`` pairs."""
    pairs = [("svc-a", 100.0), ("svc-b", 50.0)]
    summary = ConfidenceSummary(
        **_confidence_summary_kwargs(services_using_default_fallback=pairs)
    )

    # Pydantic v2 represents tuple fields as tuples (not lists).
    assert summary.services_using_default_fallback == [("svc-a", 100.0), ("svc-b", 50.0)]
    # The first pair is preserved positionally.
    assert summary.services_using_default_fallback[0][0] == "svc-a"
    assert summary.services_using_default_fallback[0][1] == 100.0


def test_confidence_summary_accepts_zero_costs_and_zero_percents() -> None:
    """The zero-current-cost case is representable."""
    summary = ConfidenceSummary(
        total_cost_high_confidence=0.0,
        total_cost_medium_confidence=0.0,
        total_cost_low_confidence=0.0,
        percent_cost_high_confidence=0.0,
        percent_cost_medium_confidence=0.0,
        percent_cost_low_confidence=0.0,
    )

    assert summary.percent_cost_high_confidence == 0.0
    assert summary.services_using_default_fallback == []


def test_confidence_summary_rejects_non_numeric_string_for_float_field() -> None:
    """A non-numeric string for a float field must raise ``ValidationError``."""
    with pytest.raises(ValidationError):
        ConfidenceSummary(
            **_confidence_summary_kwargs(total_cost_high_confidence="not a number")
        )


def test_confidence_summary_coerces_numeric_string_for_float_field() -> None:
    """Pydantic v2 default coercion: numeric strings are accepted for float fields.

    This pins down the *current* (non-strict) behaviour so a future
    switch to strict mode shows up here as a deliberate change.
    """
    summary = ConfidenceSummary(
        **_confidence_summary_kwargs(total_cost_high_confidence="123.45")
    )

    assert summary.total_cost_high_confidence == 123.45


def test_confidence_summary_missing_required_field_raises() -> None:
    """Omitting a required float raises ``ValidationError``."""
    kwargs = _confidence_summary_kwargs()
    kwargs.pop("total_cost_high_confidence")
    with pytest.raises(ValidationError):
        ConfidenceSummary(**kwargs)


# ---------------------------------------------------------------------------
# AnalyzeRow / Calculation
# ---------------------------------------------------------------------------


def _calculation() -> Calculation:
    return Calculation(multiplier=0.5, dr_additional_cost=50.0, total_with_dr=150.0)


def _analyze_row_kwargs(**overrides: object) -> dict:
    base: dict = {
        "service": "ec2",
        "current_cost": 100.0,
        "calculation": _calculation(),
        "confidence": "high",
        "confidence_source": "curated multiplier for ec2",
        "rationale": "EC2 capacity stays warm under DR.",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("level", ["high", "medium", "low"])
def test_analyze_row_accepts_each_confidence_level(level: str) -> None:
    """All three documented confidence levels are valid."""
    row = AnalyzeRow(**_analyze_row_kwargs(confidence=level))
    assert row.confidence == level


@pytest.mark.parametrize(
    "bad_value",
    ["unknown", "", "HIGH", "Low", "none", "n/a", "very_high"],
)
def test_analyze_row_rejects_confidence_outside_literal_set(bad_value: str) -> None:
    """``confidence`` is constrained to ``{"high", "medium", "low"}``."""
    with pytest.raises(ValidationError):
        AnalyzeRow(**_analyze_row_kwargs(confidence=bad_value))


def test_analyze_row_accepts_calculation_instance() -> None:
    """``calculation`` accepts a :class:`Calculation` instance."""
    calc = _calculation()
    row = AnalyzeRow(**_analyze_row_kwargs(calculation=calc))

    assert isinstance(row.calculation, Calculation)
    assert row.calculation.multiplier == 0.5
    assert row.calculation.dr_additional_cost == 50.0
    assert row.calculation.total_with_dr == 150.0


def test_analyze_row_accepts_calculation_dict() -> None:
    """``calculation`` accepts a dict with the three required keys."""
    row = AnalyzeRow(
        **_analyze_row_kwargs(
            calculation={
                "multiplier": 0.25,
                "dr_additional_cost": 25.0,
                "total_with_dr": 125.0,
            }
        )
    )

    assert isinstance(row.calculation, Calculation)
    assert row.calculation.multiplier == 0.25
    assert row.calculation.dr_additional_cost == 25.0
    assert row.calculation.total_with_dr == 125.0


def test_analyze_row_calculation_dict_with_missing_key_raises() -> None:
    """An incomplete ``calculation`` dict still triggers nested validation."""
    with pytest.raises(ValidationError):
        AnalyzeRow(
            **_analyze_row_kwargs(
                calculation={"multiplier": 0.5, "dr_additional_cost": 50.0}
            )
        )


# ---------------------------------------------------------------------------
# ErrorInfo
# ---------------------------------------------------------------------------


def test_error_info_next_steps_defaults_to_empty_list() -> None:
    """``next_steps`` is an opt-in field with an empty-list default."""
    info = ErrorInfo(code="INVALID_INPUT", message="bad input")

    assert info.next_steps == []


def test_error_info_next_steps_accepts_arbitrary_strings() -> None:
    """Arbitrary plain-text steps are preserved verbatim and in order."""
    steps = [
        "Re-run with --verbose for more detail.",
        "Check that the parameter values are in range.",
        "If the issue persists, file a bug.",
    ]
    info = ErrorInfo(
        code="INVALID_INPUT",
        message="bad input",
        next_steps=steps,
    )

    assert info.next_steps == steps
    # Mutating the input list afterwards must not mutate the model
    # (Pydantic v2 copies sequence inputs on construction).
    steps.append("a new step")
    assert len(info.next_steps) == 3


def test_error_info_next_steps_accepts_explicit_empty_list() -> None:
    """Explicitly passing an empty list behaves the same as the default."""
    info = ErrorInfo(code="AWS_ERROR", message="boom", next_steps=[])

    assert info.next_steps == []


def test_error_info_rejects_unknown_code() -> None:
    """``code`` is constrained to the documented literal set."""
    with pytest.raises(ValidationError):
        ErrorInfo(code="NOT_A_REAL_CODE", message="boom")


# ---------------------------------------------------------------------------
# Removed-models / removed-field guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "removed_name",
    ["EstimateResponse", "MultiAccountEstimateResponse", "MultiplierConfidence"],
)
def test_removed_legacy_models_are_not_importable(removed_name: str) -> None:
    """The legacy response models must not be importable from ``models``.

    Guards the removed-shape contract.
    """
    models_module = importlib.import_module("dr_cost_estimator_mcp.models")

    # Guard 1: not present as an attribute on the module.
    assert not hasattr(models_module, removed_name), (
        f"{removed_name} must be removed from dr_cost_estimator_mcp.models"
    )

    # Guard 2: ``from dr_cost_estimator_mcp.models import <name>`` raises ImportError.
    with pytest.raises(ImportError):
        importlib.import_module(
            f"dr_cost_estimator_mcp.models.{removed_name}"
        )  # not a submodule either

    # Guard 3: a fresh ``__import__`` with a fromlist also fails to bind it.
    module = __import__(
        "dr_cost_estimator_mcp.models", fromlist=[removed_name]
    )
    assert not hasattr(module, removed_name)


def test_removed_legacy_models_raise_attribute_error() -> None:
    """Direct attribute access on the module raises ``AttributeError``."""
    import dr_cost_estimator_mcp.models as models_module

    for removed_name in (
        "EstimateResponse",
        "MultiAccountEstimateResponse",
        "MultiplierConfidence",
    ):
        with pytest.raises(AttributeError):
            getattr(models_module, removed_name).__name__  # noqa: B018


@pytest.mark.parametrize(
    "model_cls",
    [CompareDrStrategiesResponse, AnalyzeDrStrategyResponse],
)
def test_pricing_validation_prompt_not_in_response_schemas(model_cls) -> None:
    """``pricing_validation_prompt`` must not appear in any response schema.

    Guards the removed-field contract. Scanning the
    serialized JSON schema catches occurrences both as a property name
    and inside descriptions.
    """
    schema = model_cls.model_json_schema()
    schema_text = json.dumps(schema)

    # Top-level field check.
    assert "pricing_validation_prompt" not in schema.get("properties", {}), (
        f"{model_cls.__name__}.properties must not contain pricing_validation_prompt"
    )

    # Full-schema text check (catches nested $defs and descriptions).
    assert "pricing_validation_prompt" not in schema_text, (
        f"{model_cls.__name__} JSON schema must not mention "
        "pricing_validation_prompt anywhere"
    )


@pytest.mark.parametrize(
    "model_cls",
    [CompareDrStrategiesResponse, AnalyzeDrStrategyResponse],
)
def test_pricing_validation_prompt_not_a_model_field(model_cls) -> None:
    """``pricing_validation_prompt`` is not declared as a Pydantic field.

    ``model_fields`` is the canonical list of Pydantic v2 fields; an
    extra-field-allowed model could still accept the value at runtime,
    so this check is complementary to the schema scan.
    """
    assert "pricing_validation_prompt" not in model_cls.model_fields
