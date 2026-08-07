# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AWS DR Cost Estimator MCP Server.

Entry point for the FastMCP-based AWS DR Cost Estimator server. Configures
logging, constructs the :class:`FastMCP` instance, registers the three
DR cost tools via :class:`DRCostEstimatorTools`, and exposes ``main()``
as the console-script entry point.

Launch over stdio with::

    uv run --directory <repo> python -m dr_cost_estimator_mcp.server
"""

import logging
import sys

from fastmcp import FastMCP
from loguru import logger as loguru_logger

from .consts import LOG_LEVEL
from .tools import DRCostEstimatorTools


def _configure_logging() -> None:
    """Configure stdlib logging and route loguru through it.

    Everything ends up on stderr, never on disk, at the level from
    ``FASTMCP_LOG_LEVEL`` (default ``WARNING``). A single loguru sink
    forwards every loguru record into the stdlib ``logging`` pipeline
    at the matching level so there is one place to reason about log
    output. Stdlib-logging callers are already handled by
    :func:`logging.basicConfig`.
    """
    # Resolve the level once; an unknown value falls back to WARNING
    # rather than raising so a bad env var does not crash the server.
    level_name = (LOG_LEVEL or "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)

    # Route stdlib logging to stderr at the configured level. ``force``
    # ensures re-initialization (e.g. under uv) replaces any earlier
    # config, including handlers inherited from parent processes.
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Bridge loguru -> stdlib. The sink extracts the loguru level,
    # looks up the matching stdlib level, and logs via a named logger
    # so records are distinguishable from direct stdlib callers. The
    # bridge is one-directional; stdlib records continue to flow
    # through ``basicConfig`` above without reaching loguru, so there
    # is no risk of a feedback loop.
    def _loguru_to_stdlib(message) -> None:
        record = message.record
        stdlib_level = getattr(logging, record["level"].name, logging.WARNING)
        logging.getLogger("dr_cost_estimator_mcp.loguru").log(
            stdlib_level, record["message"]
        )

    loguru_logger.remove()
    loguru_logger.add(sink=_loguru_to_stdlib, level=level_name)


_configure_logging()

logger = logging.getLogger(__name__)


# Instructions surfaced to MCP clients. Kept short: detailed per-tool
# documentation lives in the ``@mcp.tool`` docstrings, which FastMCP
# exposes in the tool schema.
_INSTRUCTIONS = """
# AWS DR Cost Estimator MCP Server

Estimate and compare Disaster Recovery (DR) monthly cost overhead from
live AWS Cost Explorer data. Requires AWS credentials with
``ce:GetCostAndUsage`` permission.

## Available Tools

- **list_dr_strategies** - Enumerate the supported DR strategies with
  labels and descriptions.
- **compare_dr_strategies** - Lightweight portfolio-wide
  comparison across all four DR strategies from AWS Cost Explorer
  spend. Returns per-strategy totals, top services, per-account
  totals, and a dollar-weighted confidence summary in a single call.
  Accepts an optional list of linked account ids and a ``months_back``
  window (1-24).
- **analyze_dr_strategy** - Deep-dive analysis for a single
  chosen strategy from AWS Cost Explorer spend. Returns per-service
  detail, per-account breakdown, a transparent Top-N rollup, and a
  dollar-weighted confidence summary. For services with
  usage-type-aware multipliers (RDS, OpenSearch, ELB), spend is
  split by Cost Explorer usage type group so, for example, compute and
  storage get separate multipliers.

## Supported Strategies

- `backup_restore`
- `pilot_light`
- `warm_standby`
- `active_active`

## Example Prompts

- "Compare all DR strategies for my AWS account over the last 3 months"
- "Analyze warm standby DR cost for my AWS account"
- "What DR strategies are supported?"
"""


# Create the MCP server instance.
mcp = FastMCP("dr_cost_estimator", instructions=_INSTRUCTIONS)

# Register the AWS DR cost estimator tools.
_tools = DRCostEstimatorTools()
_tools.register(mcp)


def main() -> None:
    """Start the AWS DR Cost Estimator MCP server over stdio."""
    logger.info("Starting AWS DR Cost Estimator MCP Server")
    mcp.run()


if __name__ == "__main__":
    main()
