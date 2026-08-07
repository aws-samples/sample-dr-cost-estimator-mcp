# AWS DR Cost Estimator MCP Server

This sample is provided for demonstration and educational purposes only and is not intended for production use without additional security review and testing.

Plan your AWS disaster recovery (DR) budget conversationally.
Ask your MCP-compatible assistant (Kiro, Cline, Claude, etc.) what it would cost to run DR under backup and restore, pilot light, warm standby, or active/active for your workload.
Compare all four strategies side by side in one response.

The server reads your current spend from the AWS Cost Explorer API, applies DR cost multipliers derived from real customer engagements, and returns a budgetary estimate in seconds — with per-row confidence levels so you know where the estimate is well-grounded and where it needs further validation.

For the full methodology, see [docs/methodology.md](docs/methodology.md).

## Quick start

### 1. Install

Add the server with one click. 

| Kiro | Cursor | VS Code |
|:----:|:------:|:-------:|
| [![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=dr-cost-estimator&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22git%2Bhttps%3A//github.com/aws-samples/sample-dr-cost-estimator-mcp.git%22%2C%22dr-cost-estimator-mcp%22%5D%2C%22env%22%3A%7B%22FASTMCP_LOG_LEVEL%22%3A%22WARNING%22%2C%22AWS_PROFILE%22%3A%22your-aws-profile%22%2C%22AWS_REGION%22%3A%22us-east-1%22%7D%7D) | [![Install MCP Server](https://cursor.com/deeplink/mcp-install-light.svg)](https://cursor.com/en/install-mcp?name=dr-cost-estimator&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJnaXQraHR0cHM6Ly9naXRodWIuY29tL2F3cy1zYW1wbGVzL3NhbXBsZS1kci1jb3N0LWVzdGltYXRvci1tY3AuZ2l0IiwiZHItY29zdC1lc3RpbWF0b3ItbWNwIl0sImVudiI6eyJGQVNUTUNQX0xPR19MRVZFTCI6IldBUk5JTkciLCJBV1NfUFJPRklMRSI6InlvdXItYXdzLXByb2ZpbGUiLCJBV1NfUkVHSU9OIjoidXMtZWFzdC0xIn0sImRpc2FibGVkIjpmYWxzZSwiYXV0b0FwcHJvdmUiOltdfQ==) | [![Install on VS Code](https://img.shields.io/badge/Install_on-VS_Code-FF9900?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=DR%20Cost%20Estimator&config=%7B%22command%22%3A%20%22uvx%22%2C%20%22args%22%3A%20%5B%22--from%22%2C%20%22git%2Bhttps%3A//github.com/aws-samples/sample-dr-cost-estimator-mcp.git%22%2C%20%22dr-cost-estimator-mcp%22%5D%2C%20%22env%22%3A%20%7B%22FASTMCP_LOG_LEVEL%22%3A%20%22WARNING%22%2C%20%22AWS_PROFILE%22%3A%20%22your-aws-profile%22%2C%20%22AWS_REGION%22%3A%20%22us-east-1%22%7D%2C%20%22disabled%22%3A%20false%2C%20%22autoApprove%22%3A%20%5B%5D%7D) |

Or paste this into your MCP client config manually:

```json
{
  "mcpServers": {
    "dr-cost-estimator": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aws-samples/sample-dr-cost-estimator-mcp.git", "dr-cost-estimator-mcp"],
      "env": {
        "FASTMCP_LOG_LEVEL": "WARNING",
        "AWS_PROFILE": "your-aws-profile",
        "AWS_REGION": "us-east-1"
      }
    }
  }
}
```

`uvx` fetches and runs the server from this repo with no clone or build needed.

### 2. Set up IAM permissions

Attach a policy granting `ce:GetCostAndUsage` to the IAM identity behind your `AWS_PROFILE`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DRCostEstimatorCostExplorerRead",
      "Effect": "Allow",
      "Action": "ce:GetCostAndUsage",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    }
  ]
}
```

Cost Explorer doesn't support resource-level ARNs for `GetCostAndUsage`, so `Resource` must be `*`. The condition restricts calls to the us-east-1 endpoint (the only endpoint Cost Explorer uses). For additional controls, consider [SCPs](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html) or [`aws:PrincipalTag` conditions](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html#condition-keys-principaltag).

### 3. Ask your assistant

```
"Compare all four DR strategies for my AWS account over the last 6 months."
```

That's it.

## Example prompts

- "Compare all four DR strategies for my AWS account over the last 6 months and show me estimated costs."
- "Using my `prod` AWS profile, analyze warm standby DR cost for the last 3 months."
- "For AWS accounts `111122223333` and `444455556666`, analyze pilot light DR cost over the last 3 months."
- "Estimate DR costs for the workload tagged `Project=checkout` across all my accounts."
- "Compare DR strategies for resources tagged `app=payments` or `app=ledger` in production (`env=prod`)."
- "What would warm standby cost if we keep 30% warm capacity?"

## Features

- **Three tools:** `list_dr_strategies`, `compare_dr_strategies`, `analyze_dr_strategy`
- **Four DR strategies:** backup/restore, pilot light, warm standby, active/active
- **Live Cost Explorer input** averaged over 1-24 complete months, optionally scoped by linked accounts or cost allocation tags
- **Usage-type-aware multipliers** for RDS, OpenSearch, and Elastic Load Balancing (compute vs. storage vs. backup get separate multipliers)
- **Dollar-weighted confidence** showing what fraction of spend has high-confidence vs. default-fallback multipliers
- **Per-row rationale** with confidence level and plain-language explanation
- **Per-workload estimates via tags** (`tag_filters` parameter)
- **Tunable warm-standby capacity** (0.1-1.0) to match your RTO requirements

For detailed parameters, response shapes, and example calls, see [docs/tool_reference.md](docs/tool_reference.md).

## Customizing multipliers

The DR cost multipliers live in JSON files under `dr_cost_estimator_mcp/core/data/`; edit them directly in your clone. For a readable rendering of every shipped multiplier with its rationale, see [docs/multiplier_reference.md](docs/multiplier_reference.md) (generated from the data files). For guidance on tuning multipliers for your portfolio, see [docs/methodology.md](docs/methodology.md#customizing-for-your-portfolio).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Server doesn't appear in tool list | Restart your MCP client after config changes. Set `FASTMCP_LOG_LEVEL=DEBUG` and check server logs. |
| `INVALID_INPUT` error | Check parameter ranges: `strategy` must be a valid id, `top_n` 1-500, `months_back` 1-24, account ids must be 12 digits. |
| `AWS_ERROR` | Verify `AWS_PROFILE` is set, the profile has the IAM policy above, and `months_back` covers a period with actual spend. |
| Empty estimates (no error) | All line items matched the skip list (tax, refunds, support, Savings Plans). This is expected when there's no estimable spend. |
| Numbers differ from Cost Explorer console | The tools use `UnblendedCost` at monthly granularity, excluding the current incomplete month. Console views using amortized cost or partial months will differ. |

## Security

The server is **read-only**: `ce:GetCostAndUsage` is the only AWS API call. It cannot create, modify, or delete any resource. No data is written to disk; all output goes to stderr.

## Documentation

- [Methodology](docs/methodology.md) — multiplier tiers, assumptions, worked examples, customization guidance
- [Tool Reference](docs/tool_reference.md) — parameters, response shapes, example calls
- [Multiplier Reference](docs/multiplier_reference.md) — every shipped multiplier with rationale (generated)

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file for details.
