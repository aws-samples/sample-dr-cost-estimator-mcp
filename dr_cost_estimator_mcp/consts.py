# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Configuration constants for the AWS DR Cost Estimator MCP server.

All values that are driven by environment variables or bound response
sizes live here so the rest of the package can depend on a single,
import-time-resolved source of truth.
"""

import os

# Logging level read from the FastMCP standard env var. Defaults to
# WARNING so the server stays quiet on stderr unless the operator opts
# into more detail.
LOG_LEVEL = os.environ.get("FASTMCP_LOG_LEVEL", "WARNING")

# Default value for the ``top_n`` parameter on the cost tools.
DEFAULT_TOP_N = 25

# Cap on the number of skipped-service names returned in responses to
# keep payloads bounded regardless of input size.
MAX_SKIPPED_NAMES_RETURNED = 20

# Synthetic per-account entry used when the input carries no per-account
# breakdown. Cost Explorer's GroupBy=SERVICE aggregation has no account
# dimension, so responses synthesize a single entry with these values
# covering the whole query scope (which may span many linked accounts).
# The summary generator checks for this marker so it never claims an
# account COUNT it cannot know.
CE_SYNTHETIC_ACCOUNT_ID = "unknown"
CE_SYNTHETIC_ACCOUNT_NAME = "Cost Explorer Export"
