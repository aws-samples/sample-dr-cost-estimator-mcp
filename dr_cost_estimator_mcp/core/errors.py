# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared structured-error exception class for the DR Cost Estimator.

:class:`ToolError` lives in :mod:`dr_cost_estimator_mcp.core` rather
than :mod:`dr_cost_estimator_mcp.tools` so that core-layer modules
(``aws_costs``) can raise it directly without creating a circular
dependency back into ``tools.py``.
"""

from __future__ import annotations


__all__ = ["ToolError"]


class ToolError(Exception):
    """Internal exception signaling a structured tool-level failure.

    Carries a machine-readable ``code`` (one of the values enumerated
    on :class:`~dr_cost_estimator_mcp.models.ErrorInfo`) and a
    human-readable ``message``. Tool methods catch this exception at
    their boundary and convert it into a response model with
    ``success=False`` via the tool layer's ``_error_response`` helper.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
