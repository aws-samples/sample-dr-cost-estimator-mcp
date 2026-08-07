# Security Architecture

This sample is provided for demonstration and educational purposes only and is not intended for production use without additional security review and testing.

This document provides the security architecture, threat model, and implementation guidelines for the AWS DR Cost Estimator MCP Server.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Client                                │
│  (Kiro, Cline, Claude Desktop, Amazon Q Developer CLI)          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ stdio (JSON-RPC over stdin/stdout)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   DR Cost Estimator MCP Server                   │
│                                                                  │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────┐ │
│  │  Tool Layer  │  │  Estimate Engine │  │ Multiplier Engine │ │
│  │  (tools.py)  │──│  (estimates.py)  │──│ (multipliers.py)  │ │
│  └──────┬───────┘  └──────────────────┘  └───────────────────┘ │
│         │                                                        │
│  ┌──────┴───────┐                                               │
│  │  AWS Costs   │                                               │
│  │(aws_costs.py)│                                               │
│  └──────┬───────┘                                               │
└─────────┼───────────────────────────────────────────────────────┘
          │ HTTPS (TLS 1.2+)
          ▼
┌─────────────────────┐
│  AWS Cost Explorer  │
│  (ce.us-east-1)     │
└─────────────────────┘
```

### Components

| Component | Responsibility | Trust Boundary |
|---|---|---|
| MCP Client | Sends tool requests, renders responses | External (untrusted input) |
| Tool Layer (`tools.py`) | Input validation, error shaping | Server boundary |
| Estimate Engine (`estimates.py`) | Per-service DR math, Top-N rollup | Internal |
| Multiplier Engine (`multipliers.py`) | Multiplier resolution (usage type group → curated → category → default) | Internal |
| AWS Costs (`aws_costs.py`) | boto3 session management, Cost Explorer API calls | AWS boundary |

## Threat Model

### Trust Boundaries

1. **MCP Client → Server**: All input from the MCP client is untrusted. The tool layer validates every parameter before processing.
2. **Server → AWS**: Authenticated via boto3 credential chain. Only read-only `ce:GetCostAndUsage` calls are made.

### Threat Analysis

| Threat | Attack Vector | Mitigation | Priority |
|---|---|---|---|
| Credential theft | Hardcoded secrets in source | No credentials in code; boto3 credential chain only | Critical |
| Parameter abuse | Malformed tool parameters (invalid account ids, out-of-range windows) | Type checks, 12-digit account-id validation, `months_back` and `top_n` range validation | High |
| AWS API abuse | Excessive Cost Explorer calls | Bounded call pattern (one discovery query plus at most one per-service usage-type-group query); read-only permission; rate limiting delegated to AWS service quotas | Medium |
| Information disclosure | Error messages leaking internal state | Structured `ErrorInfo` envelope with sanitized messages; no stack traces sent to the client | Medium |

### Security Controls Summary

| Control | Implementation | Verification |
|---|---|---|
| Input validation | Type checks, range validation, account-id format checks | Unit tests in `tests/test_aws_mode.py` |
| No hardcoded credentials | boto3 credential chain | ASH scan + manual review |
| Secure data loading | `json.load` for bundled multiplier data (no eval/exec) | Static analysis |
| Structured errors | `ToolError` → `ErrorInfo` envelope | All tool methods return typed responses |
| TLS in transit | boto3 default HTTPS to AWS endpoints | boto3 enforces TLS 1.2+ by default |
| Minimal IAM scope | Tools call only `get_cost_and_usage` | Regression test asserting no other client method is invoked |

## AWS Shared Responsibility Model

This tool operates under the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/):

### AWS Responsibility (Security *of* the Cloud)

- Cost Explorer API availability and endpoint security
- TLS termination at `ce.us-east-1.amazonaws.com`
- IAM policy evaluation and enforcement
- Physical infrastructure security

### Customer Responsibility (Security *in* the Cloud)

| Responsibility | Action Required |
|---|---|
| IAM policy management | Attach least-privilege policy with condition constraints (see README) |
| Credential management | Use IAM roles or temporary credentials; do not use long-lived access keys |
| Cost data handling | Treat tool output as confidential; do not commit real cost data to version control |
| Network security | Run the MCP server in a trusted environment; the stdio transport has no authentication layer |
| Dependency updates | Monitor and update Python dependencies for security patches |
| Access control | Restrict who can invoke the MCP server through client-level configuration |

## Security Guidelines by AWS Service

### AWS Cost Explorer

- **Permission**: Grant only `ce:GetCostAndUsage` (read-only)
- **Condition constraints**: Restrict to `aws:RequestedRegion: us-east-1`
- **Credential rotation**: Use IAM roles with temporary credentials where possible
- **Monitoring**: Enable CloudTrail logging for `ce:GetCostAndUsage` calls to detect unauthorized access

### boto3 Credential Management

- **Credential chain**: The server uses the standard boto3 credential resolution order (environment variables → shared credentials file → IAM role)
- **No credential storage**: The server does not store, cache, or log credentials
- **Profile isolation**: Use a dedicated AWS profile with minimal permissions for this tool
- **Session duration**: boto3 sessions are created per-request and not persisted

### Amazon S3 (Referenced in DR Strategies)

- **Block Public Access**: Enable at account and bucket level for all DR buckets
- **Encryption**: Use SSE-S3 (default) or SSE-KMS for regulated workloads
- **TLS enforcement**: Apply bucket policies requiring `aws:SecureTransport`
- **Access logging**: Enable server access logging for audit trails
- **Replication IAM**: Scope `s3:ReplicateObject` to the replication role ARN only

See [docs/methodology.md](docs/methodology.md#s3-security-best-practices-for-dr-implementations) for detailed S3 DR security configuration.

## Data Classification

| Data Type | Classification | Handling Requirements |
|---|---|---|
| Cost Explorer API response | Confidential | Returned to MCP client over stdio; not persisted to disk |
| Multiplier data (JSON) | Public | Open-source; no customer-specific information |
| Server logs (stderr) | Internal | Contains tool invocation metadata; no cost data logged |

### Data Flow Controls

- **No persistence**: The server does not write cost data to disk. All processing is in-memory.
- **No logging of sensitive data**: Cost values and account IDs are not written to stderr.
- **No outbound data exfiltration**: The server's only network destination is `ce.us-east-1.amazonaws.com`.

## Encryption

### In Transit

- **AWS API calls**: boto3 enforces TLS 1.2+ for all connections to AWS endpoints. The server does not override or weaken TLS settings.
- **MCP stdio transport**: Communication between the MCP client and server uses stdin/stdout within the same host. No network transport is involved.

### At Rest

- **Server process**: The server does not persist any data to disk during operation.
- **Multiplier data files**: Public data; encryption at rest is not required.

## MCP Server Distribution Compliance

This project is distributed as an open-source AWS sample under the MIT-0 license. It is designed to be used as a local MCP server started by an MCP client on the user's machine.

- **Distribution model**: Source code distributed via GitHub under `aws-samples`
- **License**: MIT No Attribution (MIT-0) - see [LICENSE](LICENSE)
- **Security review**: Automated Security Helper (ASH) scans are run before each release (see [CONTRIBUTING.md](CONTRIBUTING.md#pre-release-security-scan-ash))
- **No external data collection**: The server does not phone home, collect telemetry, or transmit data to third parties
- **No bundled models or datasets**: The server contains only cost multiplier data derived from public AWS pricing information
- **User control**: Users run the server locally and control all AWS credentials and data access

## Implementation Priority

Security controls are prioritized as follows:

| Priority | Control | Status |
|---|---|---|
| P0 (Critical) | No hardcoded credentials | Verified |
| P0 (Critical) | Read-only, single-permission AWS surface | Implemented with regression test |
| P1 (High) | IAM least-privilege with conditions | Documented with examples |
| P1 (High) | Structured error handling (no stack traces to client) | Implemented |
| P1 (High) | Input validation on all tool parameters | Implemented |
| P2 (Medium) | Dependency pinning | Managed via `uv.lock` |
| P2 (Medium) | Pre-release ASH scanning | Documented in CONTRIBUTING.md |
| P3 (Low) | S3 security guidance for DR implementations | Documented in methodology.md |
