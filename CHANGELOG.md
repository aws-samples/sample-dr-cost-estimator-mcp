# Changelog

All notable changes to the AWS DR Cost Estimator MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-07-18

### Added
- Initial release of the AWS DR Cost Estimator MCP Server.
- Three MCP tools: `list_dr_strategies`, `compare_dr_strategies`, `analyze_dr_strategy`.
- Four-tier multiplier resolution (usage type group → curated service → category → default) with dollar-weighted confidence summary on every response.
- Usage-type-group resolution for Amazon RDS, Amazon OpenSearch Service, and Amazon Elastic Load Balancing.
- `warm_standby_capacity` parameter (0.1-1.0, default 0.5) for scaling warm-standby compute assumptions.
- `tag_filters` parameter for per-workload estimates scoped by cost allocation tags.
- Word-boundary service-name matching with most-specific-match resolution.
- Structured error envelope with actionable remediation steps.
- Multiplier data override via the `DR_MULTIPLIERS_DIR` environment variable.
