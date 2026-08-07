# DR Multiplier Reference

<!-- GENERATED FILE - do not edit by hand. -->
<!-- Edit the JSON files under dr_cost_estimator_mcp/core/data/ and re-run: uv run python scripts/generate_multiplier_reference.py -->

Every multiplier the tools apply, with its rationale, rendered directly from the shipped data files. Multipliers express the **additional** monthly DR cost as a fraction of current primary-region spend for that line item. For how the numbers were derived and how to customize them, see [methodology.md](methodology.md).

Resolution order per line item: usage type group → curated service → category → default.

## Usage-Type-Group Multipliers (highest precedence)

Group keys are exact Cost Explorer `USAGE_TYPE_GROUP` dimension values. Spend in a supported service that no configured group captures resolves via the service's blended multiplier below. Per-group rationale lives in [methodology.md](methodology.md#usage-type-group-resolution).

### Amazon Elastic Load Balancing

| Usage type group | Backup & Restore | Pilot Light | Warm Standby | Active/Active |
|---|---:|---:|---:|---:|
| EC2: ELB - Running Hours | 0.0 | 1.0 | 1.0 | 1.0 |
| EC2: ELB - LCU Running Hours | 0.0 | 0.0 | 0.0 | 0.0 |

### Amazon OpenSearch Service

| Usage type group | Backup & Restore | Pilot Light | Warm Standby | Active/Active |
|---|---:|---:|---:|---:|
| OpenSearchService: Running Hours | 0.0 | 0.3 | 0.7 | 1.0 |

### Amazon Relational Database Service

| Usage type group | Backup & Restore | Pilot Light | Warm Standby | Active/Active |
|---|---:|---:|---:|---:|
| RDS: Running Hours | 0.0 | 0.5 | 0.5 | 1.0 |
| RDS: Storage | 1.0 | 1.0 | 1.0 | 1.0 |
| Aurora: Backup Usage | 1.0 | 1.0 | 1.0 | 1.0 |
| RDS: I/O Requests | 0.0 | 0.5 | 0.5 | 1.0 |

## Curated Service Multipliers

Entries are matched against the Cost Explorer service name (exact match first, then most-specific substring). Aliases listed under a heading share identical values and rationale.

### Amazon Elastic Compute Cloud - Compute

Matches: `amazon elastic compute cloud - compute`, `ec2-instances`

*Warm standby scales with the `warm_standby_capacity` tool parameter (value shown assumes the 50% default).*

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.0 | No provisioned compute capacity in secondary region. |
| Warm Standby | 0.5 | Scaled-down but fully functional fleet at ~50% of primary; Auto Scaling ramps to 100% on failover. |
| Active/Active | 1.0 | Full capacity in each Region so either absorbs the other's load with no ramp; total compute roughly doubles the single-Region baseline. |

### Amazon Q

Matches: `amazon q`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Per-user subscription service, not region-bound infrastructure. Nothing runs in a DR region; the subscription follows the users. |
| Pilot Light | 0.0 | Same as backup restore: no regional footprint to keep warm. |
| Warm Standby | 0.0 | Same as backup restore: subscription pricing is unaffected by DR posture. |
| Active/Active | 0.0 | Same as backup restore: one subscription regardless of how many regions the workload spans. |

### Amazon Simple Notification Service

Matches: `amazon simple notification service`, `sns`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.0 | No messages delivered to SNS topics in secondary region. |
| Warm Standby | 0.0 | No messages delivered to SNS topics in secondary region. |
| Active/Active | 0.0 | Messages split across regions. |

### Amazon Simple Queue Service

Matches: `amazon simple queue service`, `sqs`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.0 | No messages delivered to SQS queues in secondary region. |
| Warm Standby | 0.0 | No messages delivered to SQS queues in secondary region. |
| Active/Active | 0.0 | Messages split across regions. |

### Amazon Simple Storage Service

Matches: `amazon simple storage service`, `s3`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 1.0 | CRR still preferred over AWS Backup (Backup storage is ~2x S3 cost). Same replication approach. |
| Pilot Light | 1.0 | Cross-region replication (CRR). Entire bucket replicated. Versioning required. Incremental costs: primary costs + data transfer. |
| Warm Standby | 1.0 | Same as pilot light - S3 DR is identical for both strategies. |
| Active/Active | 1.0 | Cross-region replicated bucket; same cost shape as pilot light or warm standby. |

### Amazon Virtual Private Cloud

Matches: `amazon virtual private cloud`, `vpc`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.1 | The VPC line is mostly hourly charges (interface endpoints, NAT gateways) plus usage-based data processing. Pre-deploying the network skeleton in DR duplicates a small slice of endpoint hours; with no traffic there is almost no data processing. |
| Pilot Light | 0.1 | Same as backup restore: DR endpoint and NAT gateway hours run, but the usage-based majority of the line (data processing) stays near zero without traffic. |
| Warm Standby | 0.2 | DR endpoint and NAT gateway hours run continuously, and the scaled-down warm fleet generates some data processing charges; still a small fraction of the primary line. |
| Active/Active | 0.7 | Both regions carry full networking surface: NAT gateway hours and data processing in the DR region approach the primary's as traffic splits and replication flows between regions. |

### Bedrock

Matches: `bedrock`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Serverless inference billed per token; no provisioned capacity to replicate. Prompts, agents, and knowledge-base configs are redeployable artifacts. |
| Pilot Light | 0.0 | No invocations in the DR region means no charges; on-demand inference has nothing standing to keep warm. |
| Warm Standby | 0.0 | Same as pilot light for on-demand inference. Note: Provisioned Throughput, if purchased in DR, would bill directly and is not covered by this multiplier. |
| Active/Active | 0.0 | Invocations split across regions; per-token billing means total inference cost is unchanged. |

### CloudFront

Matches: `cloudfront`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.0 | Slight increase to replicate static assets in S3 to DR region, but data requests served by CloudFront will not change. |
| Warm Standby | 0.0 | Same as pilot light - CloudFront serves from edge, no DR region cost. |
| Active/Active | 0.05 | Slight increase to replicate static assets; data volumes served by CloudFront unchanged. |

### CloudWatch

Matches: `cloudwatch`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.05 | Minimal increase for metrics on data/snapshots moved into new region. |
| Pilot Light | 0.7 | Logs volume unchanged. Metrics cost increases as they exist in DR region (mostly reporting zero). Alerts duplicated in DR. |
| Warm Standby | 0.75 | Same as pilot light with slightly more metrics from warm standby compute. |
| Active/Active | 0.75 | Logs unchanged. Metrics double across regions. Alerts duplicated. |

### CloudWatch Events

Matches: `cloudwatch events`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | CloudWatch Events (EventBridge) bills per event published; rules and bus definitions are free. Nothing runs in DR, so nothing publishes. |
| Pilot Light | 0.0 | Event buses and rules deployed in DR cost nothing until events flow, and no events flow until failover. |
| Warm Standby | 0.0 | Same as pilot light: per-event billing means the idle DR bus is free even with warm compute running, unless DR-side workloads publish events. |
| Active/Active | 0.0 | Event volume splits across regions; total events published are unchanged, so no additional charge. |

### Codewhisperer

Matches: `codewhisperer`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Legacy Cost Explorer billing name for what is now Amazon Q Developer. Per-user subscription, not region-bound; nothing to replicate for DR. |
| Pilot Light | 0.0 | Same as backup restore: no regional footprint to keep warm. |
| Warm Standby | 0.0 | Same as backup restore: subscription pricing is unaffected by DR posture. |
| Active/Active | 0.0 | Same as backup restore: one subscription regardless of how many regions the workload spans. |

### DMS

Matches: `dms`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | No provisioned DMS instances in secondary region. |
| Pilot Light | 0.0 | No provisioned DMS instances in secondary region. DMS remains inactive until failover. |
| Warm Standby | 0.1 | DMS instances running at reduced capacity (~10%). Inactive until secondary becomes primary. |
| Active/Active | 0.0 | Assuming 50-50 workload split. DMS instance sizes could be reduced in both regions. |

### DocumentDB (with MongoDB compatibility)

Matches: `documentdb`, `documentdb (with mongodb compatibility)`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.03 | Cross-region snapshot copies of the data tier only (~3% of cluster spend); no DR-region compute. |
| Pilot Light | 0.8 | DocumentDB global cluster: a secondary cluster with reduced instance sizes replicates continuously. Storage-based replication means DR storage costs mirror the primary; instance compute is the smaller share. |
| Warm Standby | 0.8 | Same shape as pilot light: a reduced-size secondary cluster in the global cluster, ready to promote on failover. |
| Active/Active | 1.0 | DocumentDB has no native active-active. A full-size secondary cluster serving reads is the closest posture; the DR cluster mirrors primary compute and storage. |

### DynamoDB

Matches: `dynamodb`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.02 | PITR plus AWS Backup cross-region copy. No Global Table; storage-only cost is small fraction of baseline. |
| Pilot Light | 0.55 | Global Tables replica in DR region. Replicates writes (rWRU at ~$1.25/M) and storage; no read traffic in DR. Multiplier captures binary cost shape. |
| Warm Standby | 0.55 | Same as pilot light. Global Tables has no scaled-down replica option; either you have a replica or you don't. |
| Active/Active | 0.55 | Same as pilot light, plus DR region serves traffic. Read costs split 50/50 across regions; write costs unchanged. |

### EC2 - Other

Matches: `ec2 - other`, `ec2-other`

*Warm standby scales with the `warm_standby_capacity` tool parameter (value shown assumes the 50% default).*

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Stateless EC2 tier recovers from AMIs and IaC; no EBS snapshots copied to DR. AMI storage is per-image and negligible. |
| Pilot Light | 0.0 | Stateless EC2 tier recovers from AMIs and IaC; no EBS snapshots copied to DR. |
| Warm Standby | 0.5 | EBS scales with the DR EC2 footprint, ~50% at warm standby. |
| Active/Active | 1.0 | EBS mirrors full compute in both Regions; volumes duplicated at 100%. |

### ECR

Matches: `ecr`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 1.0 | Cross-region replication: full storage cost mirrored in DR region. |
| Pilot Light | 1.0 | Cross-region replication: full storage cost mirrored in DR region. |
| Warm Standby | 1.0 | Cross-region replication: full storage cost mirrored in DR region. |
| Active/Active | 1.0 | Cross-region replication: full storage cost mirrored in DR region. |

### Elastic Container Service

Matches: `ecs`, `elastic container service`

*Warm standby scales with the `warm_standby_capacity` tool parameter (value shown assumes the 50% default).*

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | ECS task definitions are config; nothing to back up. Container images are billed separately under ECR. |
| Pilot Light | 0.0 | No ECS tasks running in secondary region. |
| Warm Standby | 0.5 | Scaled-down but fully functional ECS tasks at ~50% of primary; Auto Scaling ramps to 100% on failover. |
| Active/Active | 1.0 | Full-capacity ECS tasks in each Region so either absorbs the other's load with no ramp; total task compute roughly doubles. |

### Elastic Container Service For Kubernetes

Matches: `eks`, `elastic container service for kubernetes`, `elastic kubernetes service`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | No DR-region cluster, so no control plane fee until failover bring-up. Container images replicate under ECR, not this line. |
| Pilot Light | 1.0 | The EKS control plane bills a flat hourly fee per cluster whether or not workloads run, so a pilot-light cluster doubles this line even at zero nodes. EC2 worker nodes bill under EC2, not here. |
| Warm Standby | 1.0 | Same as pilot light: the control plane fee is flat, so the DR cluster doubles this line regardless of warm node count. Warm EC2 node capacity bills under EC2. EKS Fargate pods, if used, add to this line only while running. |
| Active/Active | 1.0 | A full control plane runs in each region, doubling this line. Worker capacity in both regions bills under EC2 (EKS Fargate pod usage would land on this line). |

### Elastic File System

Matches: `efs`, `elastic file system`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.2 | AWS Backup cross-region copy at warm-tier pricing; ~20% of source storage cost. |
| Pilot Light | 1.0 | EFS Replication active to DR. DR storage costs full primary rate plus replication transfer. |
| Warm Standby | 1.0 | Same as pilot light. EFS Replication is the same primitive across postures. |
| Active/Active | 1.0 | EFS does not support multi-region active/active. Treat as warm standby with DR-region mount targets active. |

### Elastic Load Balancing

Matches: `ec2-elb`, `elastic load balancing`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.2 | Pay for running load balancers in secondary region. No consumption-based costs since not serving workloads. ~20% for ELB hours. |
| Warm Standby | 0.2 | Same as pilot light - ELBs running but minimal traffic. |
| Active/Active | 0.2 | Second load balancer runs in the DR Region (added hours); traffic splits across Regions so total capacity-unit (LCU) charges don't increase. |

### Elastic MapReduce

Matches: `elastic mapreduce`, `emr`

*Warm standby scales with the `warm_standby_capacity` tool parameter (value shown assumes the 50% default).*

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.0 | No provisioned EMR clusters in secondary region. |
| Warm Standby | 0.5 | Scaled-down EMR cluster at ~50% of primary; Auto Scaling ramps to 100% on failover. |
| Active/Active | 1.0 | Full-capacity clusters in each Region so either absorbs the other's load with no ramp; total compute roughly doubles. |

### ElastiCache

Matches: `elasticache`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Cache data is ephemeral; no backup needed. The cache rebuilds from the source database on failover. |
| Pilot Light | 0.3 | Global Datastore secondary cluster at ~30% of primary node count. |
| Warm Standby | 0.65 | Global Datastore secondary at ~65% of primary; ready to absorb traffic on failover. |
| Active/Active | 0.65 | Same as warm standby. Global Datastore is single-leader; true active/active requires app-level dual-write. |

### FSx

Matches: `fsx`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.5 | AWS Backup cross-region copy of the file system. Backup vault storage plus incremental transfer; 0.5 is conservative and assumes multi-snapshot retention in the DR region. No DR-region file system runs. |
| Pilot Light | 1.0 | A standing DR file system at full provisioned storage and throughput capacity (FSx bills provisioned capacity whether used or not), kept in sync via SnapMirror (FSx for ONTAP) or scheduled AWS Backup copies (other FSx types). |
| Warm Standby | 1.0 | Same as pilot light: the standing DR file system bills its full provisioned capacity across postures. |
| Active/Active | 1.0 | FSx has no native multi-region active/active. Treat as warm standby with the DR file system live and mounted. |

### GuardDuty

Matches: `guardduty`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.1 | GuardDuty bills per region on analyzed volume: CloudTrail events, VPC Flow Logs, and DNS query logs. Enabling it in the DR region (best practice regardless of DR posture) analyzes only the small event volume a mostly-empty region generates. |
| Pilot Light | 0.1 | Same basis as backup restore: the DR region's analyzed volume is driven by its tiny footprint (replication endpoints, standby data services), a small fraction of the busy primary. |
| Warm Standby | 0.1 | The warm fleet raises DR-region flow-log and DNS-log volume somewhat, but analyzed volume still trails the traffic-serving primary by a wide margin. |
| Active/Active | 0.1 | Analyzed traffic volume splits across regions rather than doubling; the residual increase is the per-region baseline of CloudTrail management activity now analyzed in two regions. |

### Key Management Service

Matches: `key management service`, `kms`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.8 | Same as pilot light - keys needed for encrypted data at rest. |
| Pilot Light | 0.8 | Key creation ($1/mo each) + reduced API usage (encrypt/decrypt). Traffic not active so API usage decreases slightly. |
| Warm Standby | 0.8 | Same as pilot light: replica keys carry the per-key monthly fee regardless of posture, and API call volume tracks data replication activity, which does not change between standby postures. |
| Active/Active | 1.0 | Keys and API usage double across both regions for all activities including read-only replication. |

### Kinesis Analytics

Matches: `kinesis`, `kinesis analytics`, `kinesis firehose`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | No Kinesis charges in DR. All streams created upon bring-up. |
| Pilot Light | 0.25 | Costs for maintaining streams in DR. No PUT payload costs. Based on provisioned shards in secondary. |
| Warm Standby | 0.25 | Same as pilot light: provisioned shard capacity in DR bills the same whether compute is warm or off, and no ingestion means no payload charges either way. |
| Active/Active | 0.0 | Data ingestion and retrieval split across two regions. |

### Lambda

Matches: `lambda`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Nothing provisioned in DR region. |
| Pilot Light | 0.0 | No Lambda executions in secondary region. |
| Warm Standby | 0.0 | No Lambda executions in secondary region. |
| Active/Active | 0.0 | Executions split across two regions. |

### Managed Streaming For Apache Kafka

Matches: `managed streaming for apache kafka`, `msk`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.05 | Tiered storage to S3 with cross-region replication. No DR-region cluster. |
| Pilot Light | 0.65 | Small DR cluster (~33% of primary brokers) plus MSK Replicator replicating all topics. Replicator cost scales with throughput, not posture. |
| Warm Standby | 1.0 | DR cluster at ~67% of primary brokers plus MSK Replicator. Ready to absorb traffic on failover. |
| Active/Active | 1.9 | Full mirror DR cluster plus bidirectional MSK Replicator; both regions serve traffic. |

### Neptune

Matches: `neptune`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.4 | Neptune cross-region snapshot copies are full copies each time (no incremental cross-region backups), so recurring data transfer plus DR-region backup storage add up to a substantial fraction of cluster spend. |
| Pilot Light | 0.8 | Neptune global database: a secondary cluster with reduced instance sizes replicates continuously. DR storage and replicated write I/Os mirror the primary; reduced compute keeps the total below 1x. |
| Warm Standby | 0.8 | Same shape as pilot light: a reduced-size secondary cluster in the global database, ready to promote on failover. |
| Active/Active | 0.8 | Neptune has no native active-active; the secondary cluster in a global database is read-only. The posture is the same reduced-size secondary as warm standby, so the cost matches warm standby. |

### OpenSearch Service

Matches: `opensearch`, `opensearch service`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.025 | Manual snapshots to S3 in DR region. No DR-region cluster running. |
| Pilot Light | 0.3 | Cross-cluster replication (CCR) follower at ~30% of primary cluster size. |
| Warm Standby | 0.7 | CCR follower at ~70% of primary cluster size, ready to absorb traffic on failover. |
| Active/Active | 1.0 | OpenSearch has no native active/active (CCR is single-leader). Two equally-sized clusters with bidirectional CCR; non-canonical. |

### Redshift

Matches: `redshift`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.01 | Cross-region snapshot copy to S3 in DR. No DR-region cluster; storage-only cost is small fraction of cluster compute. |
| Pilot Light | 0.01 | Same as backup restore: snapshot-based recovery, no DR-region cluster running. |
| Warm Standby | 0.5 | Scaled-down DR cluster (~50% of primary node count) running continuously, ready to absorb traffic on failover. |
| Active/Active | 0.5 | Redshift has no native active/active. Closest pattern is warm standby with cluster running 24/7; treat as informational. |

### Relational Database Service

Matches: `rds`, `relational database service`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.03 | Cross-region automated backups of the data tier (~3% of cluster compute); no DR-region compute. |
| Pilot Light | 0.5 | One cross-Region read replica at the same instance class as primary (best practice); one instance of a writer+reader pair = 50%. |
| Warm Standby | 0.5 | Scaled-down DR cluster sized to ~50% of primary writer+reader capacity, ready to absorb traffic. |
| Active/Active | 1.0 | A second full cluster (compute and storage) with bidirectional replication (e.g., via DMS). Cross-region replication traffic is workload-dependent and out of model. |

### Resilience Hub

Matches: `resilience hub`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Resilience Hub is the DR assessment tooling itself, billed per application assessed; it is not workload infrastructure that gets a DR replica. |
| Pilot Light | 0.0 | Same as backup restore: assessment tooling, not a DR target. |
| Warm Standby | 0.0 | Same as backup restore: assessment tooling, not a DR target. |
| Active/Active | 0.0 | Same as backup restore: assessment tooling, not a DR target. |

### Shield

Matches: `shield`

| Strategy | Multiplier | Rationale |
|---|---:|---|
| Backup & Restore | 0.0 | Shield Advanced is one monthly subscription per consolidated-billing family covering all subscribed accounts and regions; protecting DR-region resources adds no second subscription. |
| Pilot Light | 0.0 | Same subscription covers DR-region resources. Usage-based data-transfer-out fees track protected traffic, which is near zero in standby. |
| Warm Standby | 0.0 | Same subscription covers DR-region resources; standby traffic keeps usage fees negligible. |
| Active/Active | 0.0 | One subscription still covers both regions. Usage-based fees follow traffic, which splits rather than doubles. |

## Category Fallbacks

Services without a curated entry match a category by keyword and use the category defaults.

| Category | Backup & Restore | Pilot Light | Warm Standby | Active/Active | Keywords | Rationale |
|---|---:|---:|---:|---:|---|---|
| compute_serverless | 0.0 | 0.0 | 0.0 | 0.0 | lambda, app runner | Pure event-driven serverless compute (Lambda, App Runner) is billed per invocation; deployments in the DR region cost nothing until traffic arrives. Note: Lambda Provisioned Concurrency is available for warm-capacity scenarios that need pre-warmed runtimes; if used in DR, that line item will appear in the bill and be priced directly. |
| compute_server | 0.0 | 0.0 | 0.5 | 1.0 | ec2, ecs, eks, fargate, batch, lightsail, elastic beanstalk, container, compute | Server-shaped or task-shaped compute (EC2, ECS/EKS Fargate, Batch, Beanstalk) runs no DR-region capacity at backup-restore or pilot-light. Warm standby runs ~50% of primary capacity per AWS Well-Architected guidance, with Auto Scaling configured to ramp to 100% on failover. Active/active runs full capacity in each Region so either can absorb the other's load with no ramp, roughly doubling total compute. |
| data | 0.05 | 0.3 | 0.8 | 1.0 | s3, rds, dynamo, aurora, redshift, neptune, documentdb, elasticache, memorydb, keyspaces, timestream, qldb, ledger, database, storage, efs, fsx, backup, glacier | Data services replicate continuously across DR postures. Backup restore = snapshot-only; pilot light = small standing replica or storage-only replication; warm standby = scaled-down full replica running at the DR region; active/active = full mirror with replication overhead. |
| networking | 0.0 | 0.3 | 0.5 | 0.5 | vpc, route 53, cloudfront, elb, load balancer, direct connect, transit gateway, global accelerator, api gateway, network, nat | DR-region load balancers, NAT gateways, VPC endpoints, and similar networking surface are pre-deployed at pilot-light for fast cutover and run at ~50% of primary surface for warm standby and active/active. Edge-only services like Route 53 and CloudFront resolve at curated entries first and may overshoot the category default; their absolute cost is small enough that the difference is rounding. |
| streaming | 0.0 | 0.1 | 0.25 | 0.5 | kinesis, kafka, msk, sqs, sns, eventbridge, mq, firehose, streaming | Streaming services have provisioned capacity costs (shards, partitions) even without traffic. |
| management | 0.05 | 0.1 | 0.15 | 0.2 | cloudwatch, cloudtrail, config, guardduty, security hub, inspector, macie, detective, shield, waf, kms, secrets manager, systems manager, trusted advisor, x-ray, certificate, iam | Security and management services have small incremental costs for monitoring DR region resources. |

## Portfolio Default (last resort)

| Backup & Restore | Pilot Light | Warm Standby | Active/Active | Rationale |
|---:|---:|---:|---:|---|
| 0.05 | 0.1 | 0.25 | 0.5 | No specific guidance available. Using conservative general estimate. |

## Excluded Line Items (skip list)

Service names containing any of these substrings are excluded from DR estimates entirely:

`tax`, `refund`, `total costs`, `support`, `savings plans`, `ocb`, `cost explorer`, `budgets`, `registrar`, `marketplace`, `data transfer`
