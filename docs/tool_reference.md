# Tool Reference

Full parameter tables, response-shape tables, and example calls/responses for the three tools registered on the server.
See the [README's Available tools section](../README.md#available-tools) for a one-line summary of each tool.

All tools return a Pydantic-validated response with a top-level `success` boolean.
On failure, `success` is `false` and an `error` object with `code`, `message`, and `next_steps` is populated.
On success, the tool-specific payload fields are populated as documented in the following sections.

## `list_dr_strategies`

Returns the four supported DR strategies in canonical order.

**Parameters:** None.

**Response shape (`ListStrategiesResponse`):**

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | `true` on success |
| `strategies` | `list[Strategy]` | Each entry has `id`, `label`, and `description` |
| `error` | `ErrorInfo \| null` | Populated only on failure |

**Example call (tool arguments):**

```json
{}
```

**Example response (excerpt):**

```json
{
  "success": true,
  "strategies": [
    {
      "id": "backup_restore",
      "label": "Backup & Restore",
      "description": "Lowest-cost DR: data is backed up to the DR region; compute is re-provisioned on failover."
    },
    { "id": "pilot_light",   "label": "Pilot Light",   "description": "..." },
    { "id": "warm_standby",  "label": "Warm Standby",  "description": "..." },
    { "id": "active_active", "label": "Active/Active", "description": "..." }
  ]
}
```

## Shared parameters

`compare_dr_strategies` and `analyze_dr_strategy` accept the same Cost Explorer scoping parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `months_back` | `int` | `3` | Number of complete months of Cost Explorer history to average across. Range: 1-24 |
| `linked_account_ids` | `list[str] \| null` | `null` | Optional list of 12-digit AWS account ids to restrict the query to |
| `profile` | `str \| null` | `null` | Optional named AWS profile. Defaults to the boto3 credential chain |
| `region` | `str \| null` | `null` | Optional region for the boto3 session. Cost Explorer itself is always accessed through `us-east-1` |
| `top_n` | `int` | `25` | Number of top services (by current cost) to keep before rolling the remainder into a single `Other Services (<k> services)` row. Range: 1-500 |
| `include_all_services` | `bool` | `false` | When `true`, return one row per DR-eligible service with no Top-N rollup |
| `warm_standby_capacity` | `float` | `0.5` | Fraction of primary compute capacity kept warm in DR for the `warm_standby` strategy. Range: 0.1-1.0. Rescales fleet-shaped compute services (EC2 instances, EBS, ECS, EMR, server-compute category) relative to the 50% baseline; databases, storage, and control-plane fees are unaffected. A non-default value appends a warning naming the assumption |
| `tag_filters` | `dict[str, list[str]] \| null` | `null` | Cost-allocation-tag scope for per-workload estimates, e.g. `{"Project": ["checkout", "payments"], "Environment": ["prod"]}`. Keys are ANDed; values within a key are ORed. Applied to every Cost Explorer query (discovery and usage-type-group detail). At most 10 keys and 20 values per key. Tag keys must be activated as cost allocation tags in the Billing console; only tagged spend is counted, so untagged workload resources are excluded and a warning states this on every tag-scoped response |

RDS, OpenSearch, and Elastic Load Balancing spend is always split by Cost Explorer usage type group, with each group resolved through its own multiplier and any uncaptured spend resolving via the blended service multiplier; there is no parameter to enable or disable this (see [Usage-Type-Group Resolution](methodology.md#usage-type-group-resolution) in the methodology doc).

## Shared response objects

**`ErrorInfo`** - populated only when `success` is `false`.

| Field | Type | Description |
|---|---|---|
| `code` | `Literal["INVALID_INPUT", "AWS_ERROR"]` | Machine-readable error code |
| `message` | `str` | One-line human-readable error message |
| `next_steps` | `list[str]` | 1-3 plain-text remediation suggestions |

**`Totals`** - portfolio or per-account aggregate.

| Field | Type | Description |
|---|---|---|
| `total_current_cost` | `float` | Sum of `current_cost` across rows, USD |
| `total_dr_additional_cost` | `float` | Sum of `dr_additional_cost`, USD |
| `total_with_dr` | `float` | Sum of `total_with_dr`, USD |
| `dr_as_percent_of_current` | `float` | `total_dr_additional_cost / total_current_cost * 100`, two decimals; `0.0` when the denominator is zero |

**`ConfidenceSummary`** - dollar-weighted confidence across the portfolio.

| Field | Type | Description |
|---|---|---|
| `total_cost_high_confidence` | `float` | Sum of `current_cost` on rows with `confidence == "high"` (usage-type-group tier), USD |
| `total_cost_medium_high_confidence` | `float` | Same, for `"medium-high"` (curated tier) |
| `total_cost_medium_confidence` | `float` | Same, for `"medium"` (category tier) |
| `total_cost_low_confidence` | `float` | Same, for `"low"` (default tier, includes the Top-N rollup row) |
| `percent_cost_<level>_confidence` | `float` | The level's total expressed as a percent of total current cost, two decimals; `0.0` when total is zero |
| `line_item_count_by_confidence` | `dict[str, int]` | Row counts per confidence level |
| `services_using_default_fallback` | `list[tuple[str, float]]` | `(service_name, monthly_cost)` pairs for `"low"` rows, cost-descending, capped at 20 |

**`TopNRollup`** - transparent summary of services rolled into "Other Services" (analyze responses only).

| Field | Type | Description |
|---|---|---|
| `rolled_up_services_count` | `int` | Number of services aggregated into the rollup row |
| `rolled_up_services_sample` | `list[tuple[str, float]]` | First 20 `(service_name, monthly_cost)` pairs, cost-descending |
| `rolled_up_total_cost` | `float` | Sum of monthly cost across rolled-up services, USD |

## `compare_dr_strategies`

Lightweight portfolio-wide comparison across all four DR strategies from live Cost Explorer spend.
Returns per-strategy totals, top services, per-account totals, and a dollar-weighted `confidence_summary` in a single call.
No per-service-per-account detail (use `analyze_dr_strategy` for that).

**Parameters:** The [shared parameters](#shared-parameters).

**Response shape (`CompareDrStrategiesResponse`):**

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | `true` on success |
| `summary` | `str` | 2-4 sentence plain-text summary; empty string on error |
| `total_current_cost` | `float` | Portfolio current monthly cost across all returned services, USD |
| `per_strategy_totals` | `dict[str, PerStrategyTotals]` | Portfolio totals keyed by strategy id (`backup_restore`, `pilot_light`, `warm_standby`, `active_active`); each value carries `total_dr_additional_cost`, `total_with_dr`, `dr_as_percent_of_current` |
| `top_services` | `list[ComparedServiceRow]` | Top-N service rows; each carries `service`, `current_cost`, and a `per_strategy` map of `PerStrategyComparedCost` (`multiplier`, `dr_additional_cost`, `total_with_dr`, `confidence`, `confidence_source`) |
| `per_account_totals` | `list[PerAccountTotalsRow]` | Per-account totals with `account_id`, `account_name`, `total_current_cost`, and a `per_strategy` map of `PerStrategyAccountTotals` (`total_dr_additional_cost`, `total_with_dr`) |
| `confidence_summary` | `ConfidenceSummary \| null` | Dollar-weighted confidence summary; `null` on error |
| `warnings` | `list[str]` | Non-fatal warnings (e.g. summary fallback) |
| `error` | `ErrorInfo \| null` | Populated only on failure |

**Example call (tool arguments):**

```json
{
  "months_back": 3,
  "profile": "prod",
  "linked_account_ids": ["111122223333", "444455556666"],
  "top_n": 25
}
```

**Example response (excerpt):**

```json
{
  "success": true,
  "summary": "Across all spend visible to the queried Cost Explorer scope, total monthly spend is $60,000.00. Cheapest DR strategy is Backup & Restore at +$6,200.00/mo; most expensive is Active/Active at +$52,000.00/mo. Largest current-cost driver: Amazon Relational Database Service ($40,000.00/mo).",
  "total_current_cost": 60000.0,
  "per_strategy_totals": {
    "warm_standby": {
      "total_dr_additional_cost": 25000.0,
      "total_with_dr": 85000.0,
      "dr_as_percent_of_current": 41.67
    }
  },
  "top_services": [
    {
      "service": "Amazon Relational Database Service",
      "current_cost": 40000.0,
      "per_strategy": {
        "warm_standby": {
          "multiplier": 0.5,
          "dr_additional_cost": 20000.0,
          "total_with_dr": 60000.0,
          "confidence": "high",
          "confidence_source": "usage-type-group split for Amazon Relational Database Service"
        }
      }
    },
    {
      "service": "Other Services (12 services)",
      "current_cost": 20000.0,
      "per_strategy": {
        "warm_standby": {
          "multiplier": 0.25,
          "dr_additional_cost": 5000.0,
          "total_with_dr": 25000.0,
          "confidence": "low",
          "confidence_source": "Top-N rollup of 12 services; multiplier is the cost-weighted average across rolled-up services"
        }
      }
    }
  ],
  "per_account_totals": [
    {
      "account_id": "unknown",
      "account_name": "Cost Explorer Export",
      "total_current_cost": 60000.0,
      "per_strategy": {
        "warm_standby": { "total_dr_additional_cost": 25000.0, "total_with_dr": 85000.0 }
      }
    }
  ],
  "confidence_summary": {
    "total_cost_high_confidence": 40000.0,
    "total_cost_medium_high_confidence": 0.0,
    "total_cost_medium_confidence": 0.0,
    "total_cost_low_confidence": 20000.0,
    "percent_cost_high_confidence": 66.67,
    "percent_cost_medium_high_confidence": 0.0,
    "percent_cost_medium_confidence": 0.0,
    "percent_cost_low_confidence": 33.33,
    "services_using_default_fallback": [["Other Services (12 services)", 20000.0]]
  },
  "warnings": []
}
```

Cost Explorer's `GroupBy=SERVICE` aggregation does not carry per-account information, so the per-account view contains a single synthesized entry with `account_id: "unknown"` and `account_name: "Cost Explorer Export"` covering the whole query scope.
Use `linked_account_ids` to scope the query itself to specific accounts.

## `analyze_dr_strategy`

Single-call deep-dive for a chosen DR strategy: per-service detail with rationale, a per-account breakdown, and a transparent Top-N rollup, all with dollar-weighted confidence.

**Parameters:** The [shared parameters](#shared-parameters), plus:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `strategy` | `str` | (required) | One of `backup_restore`, `pilot_light`, `warm_standby`, `active_active` |

**Response shape (`AnalyzeDrStrategyResponse`):**

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | `true` on success |
| `summary` | `str` | 2-4 sentence plain-text summary; empty string on error |
| `strategy` | `str \| null` | Strategy id the analysis was produced for |
| `strategy_label` | `str \| null` | Human-readable strategy label (e.g. `"Warm Standby"`) |
| `estimates` | `list[AnalyzeRow]` | Portfolio per-service rows. Each carries `service`, `current_cost`, a `calculation` block (`multiplier`, `dr_additional_cost`, `total_with_dr`), `confidence`, `confidence_source`, and `rationale`. Includes the synthetic `Other Services (<k> services)` row when a Top-N rollup occurred |
| `totals` | `Totals \| null` | Portfolio totals across `estimates` |
| `per_account` | `list[PerAccountAnalysis]` | Per-account sections with `account_id`, `account_name`, `estimates`, and `totals` |
| `confidence_summary` | `ConfidenceSummary \| null` | Dollar-weighted confidence summary |
| `top_n_rollup` | `TopNRollup \| null` | Transparent rollup metadata; `null` when `include_all_services=true` or no rollup occurred |
| `warnings` | `list[str]` | Non-fatal warnings |
| `error` | `ErrorInfo \| null` | Populated only on failure |

**Example call (tool arguments):**

```json
{
  "strategy": "warm_standby",
  "months_back": 3,
  "profile": "prod"
}
```

**Example response (excerpt):**

```json
{
  "success": true,
  "summary": "Under Warm Standby, total monthly spend is $60,000.00 with +$25,000.00/mo additional DR cost (41.7% of current). Largest DR cost driver: Amazon Relational Database Service (+$20,000.00/mo).",
  "strategy": "warm_standby",
  "strategy_label": "Warm Standby",
  "estimates": [
    {
      "service": "Amazon Relational Database Service",
      "current_cost": 40000.0,
      "calculation": {
        "multiplier": 0.5,
        "dr_additional_cost": 20000.0,
        "total_with_dr": 60000.0
      },
      "confidence": "high",
      "confidence_source": "usage-type-group split for Amazon Relational Database Service",
      "rationale": "Split by usage type group: Running Hours at 0.50. Dominant cost driver: Running Hours."
    }
  ],
  "totals": {
    "total_current_cost": 60000.0,
    "total_dr_additional_cost": 25000.0,
    "total_with_dr": 85000.0,
    "dr_as_percent_of_current": 41.67
  },
  "per_account": [
    {
      "account_id": "unknown",
      "account_name": "Cost Explorer Export",
      "estimates": [],
      "totals": {
        "total_current_cost": 60000.0,
        "total_dr_additional_cost": 25000.0,
        "total_with_dr": 85000.0,
        "dr_as_percent_of_current": 41.67
      }
    }
  ],
  "top_n_rollup": {
    "rolled_up_services_count": 12,
    "rolled_up_services_sample": [
      ["AmazonCloudWatch", 3388.81],
      ["AWS Lambda", 3250.02]
    ],
    "rolled_up_total_cost": 20000.0
  }
}
```
