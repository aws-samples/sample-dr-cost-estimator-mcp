# DR Cost Estimation Methodology

## The Problem

You have a production workload running in a single AWS region and you want to know: "What would it cost to add disaster recovery?" Answering that question manually means opening the AWS Pricing Calculator, configuring each service for the DR region, and guessing at capacity - a process that can take days for a complex account and produces a spreadsheet that's stale the moment your spend changes.

## The Approach

This tool takes a different path. Instead of building a DR estimate from scratch, it starts from what you're already spending and applies a **multiplier** to each service based on how that service behaves under each DR strategy. The multiplier is a *DR ratio*: it represents the *additional* monthly cost of running DR as a fraction of your current primary-region spend.

```
DR cost for a service = current cost × DR ratio
```

For each AWS service, the ratio is derived from the DR design and rationale for that service under the chosen strategy: what runs in the recovery region, at what capacity, and what replicates continuously.

For example, if you spend $10,000/month on Amazon RDS and the warm-standby multiplier for RDS is 0.50, the tool estimates $5,000/month in additional DR cost for that service - reflecting a scaled-down reader (~50% of a primary writer-plus-reader pair) in the secondary region, cross-region data transfer, and storage replication.

The four strategies follow the [Disaster Recovery of Workloads on AWS](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-workloads-on-aws.html) whitepaper: backup and restore, pilot light, warm standby, and multi-site active/active.
The model targets workloads that need multi-Region DR to establish a bounded recovery time from Region-level impairment.
Most workloads can meet their resilience objectives in a single Region using multiple Availability Zones; for guidance on whether multi-Region is the right choice for a workload, see [AWS Multi-Region Fundamentals](https://docs.aws.amazon.com/prescriptive-guidance/latest/aws-multi-region-fundamentals/introduction.html).

## Key Assumptions

1. **You have a running primary region.** The tool estimates the *incremental* cost of adding DR, not the total cost of running in two regions from scratch.

2. **Current spend is a reasonable proxy for DR sizing.** The multiplier approach assumes your DR-region resource sizing scales proportionally with your primary-region spend. This holds well for data services (storage replication is proportional to data volume) and reasonably well for compute (warm-standby capacity is a fraction of primary).

3. **Multipliers are strategy-specific.** Each strategy targets a different recovery point objective (RPO) and recovery time objective (RTO) band, and the same service has different DR cost profiles depending on the strategy:
   - **Backup & Restore** (RPO and RTO in hours): Only backups (snapshots, S3 copies) cross regions. No compute or provisioned capacity runs in DR until failover; you provision all resources and restore from backup at recovery time. Cost drivers are snapshot storage in the DR region and the data transfer to ship snapshots there. Snapshot retention policy is the primary lever - see *Backup retention assumption* below.
   - **Pilot Light** (RPO in minutes, RTO in tens of minutes): The minimal infrastructure needed for a fast recovery runs continuously - primarily live data replication (DynamoDB Global Tables, Aurora Global Database, S3 CRR) and networking - while compute is off until failover. You pay for data replication but not for idle compute.
   - **Warm Standby (~50% capacity)** (RPO in seconds to minutes, RTO in minutes): A scaled-down but fully functional copy of production runs in the secondary region. Compute services run at ~50% of primary. Data services replicate fully. On failover you scale out the existing fleet rather than provisioning from scratch, so the secondary region can serve traffic immediately while Auto Scaling ramps to full capacity - this is what gives warm standby a lower RTO than pilot light.
   - **Active/Active** (near-zero RPO and RTO): Both regions serve traffic simultaneously, with requests routed by geography, latency, or load. By default, full capacity runs in each region so either can absorb the other's load with no scaling ramp, which means provisioned compute roughly doubles. Advanced users who load-balance 50/50 can run each region at half capacity, but that takes a dependency on Auto Scaling during failover. Data services with native multi-region replication (DynamoDB Global Tables, S3 CRR) can cost less than warm standby because there is no idle standby capacity.

4. **The estimate is directional, not precise.** Multipliers are derived from analysis of typical DR architectures for each service. Your actual cost will depend on specific configuration choices (instance sizes, storage tiers, replication modes). Treat the output as a planning-quality estimate for strategy comparison, not a procurement-grade quote.

## How Multipliers Were Derived

The multipliers were developed through a structured analysis process applied to each AWS service:

1. **Identify the charge model.** Understand what drives cost for the service (compute hours, storage volume, API requests, data transfer, provisioned capacity).
2. **Map DR behavior per strategy.** For each of the four strategies, determine what components run in the secondary region and at what capacity.
3. **Estimate the incremental fraction.** Express the additional DR cost as a fraction of primary-region spend, accounting for the charge model.

For example, the analysis for Amazon DynamoDB considered:
- The service bills for read capacity, write capacity, and storage separately
- Multi-region replication (Global Tables) charges writes at a higher rate than single-region writes
- Storage in the secondary region mirrors the primary, adding a proportional cost
- Data transfer between regions adds a smaller but consistent overhead
- Net result: ~0.55× additional cost across pilot light, warm standby, and active/active (Global Tables has no scaled-down replica option - you either have a replica or you don't - so the value is flat across the running strategies)

This charge-model-first approach means services with usage-based pricing (Lambda, SNS, SQS) naturally get low or zero multipliers in standby configurations, while services with provisioned or storage-based pricing (RDS, DynamoDB, S3) get higher multipliers reflecting continuous replication costs.

### Cross-Region Data Transfer Is Out of Model

The multipliers deliberately do not include a surcharge for cross-region replication traffic. Replication transfer scales with your write and change rate, not with the standing-infrastructure posture the multipliers model, so any flat percentage would be false precision - a write-heavy workload can see transfer costs several times what a read-heavy workload sees at the same spend level. Where a service's replication mechanism has an unavoidable transfer component (S3 CRR, DynamoDB Global Tables), the directional tolerance of the estimate absorbs it. If you know your change rate, model transfer explicitly: estimate replicated GB/month at the inter-region rate for your region pair and add it as a line item, or tune the affected service multipliers upward in your override directory.

### Warm Standby Compute Assumption

For compute services in warm standby, the model assumes **~50% of primary capacity** running in the secondary region - the AWS Well-Architected midpoint for a scaled-down but fully functional fleet. It's enough capacity to absorb initial failover traffic while Auto Scaling ramps to 100%. The exact percentage varies by workload (larger fleets may sit lower at 10-25%; very small fleets often round up to 50% so DR stays "fully functional"), but 50% provides a reasonable middle ground for budgetary planning.

Because this is the single assumption most workloads want to tune, both live tools accept a **`warm_standby_capacity`** parameter (0.1-1.0, default 0.5). It linearly rescales the warm-standby multiplier for fleet-shaped compute - EC2 instances, EBS, ECS, EMR, and anything resolving through the server-compute category - relative to the 50% baseline: a 25% warm fleet turns EC2's 0.50 into 0.25, and a 100% warm fleet turns it into 1.00 (full capacity, matching active/active). Services whose warm posture is not a generic fleet percentage ignore the parameter: control-plane fees (EKS), replica-count-driven databases (RDS), replication followers sized by their own assumptions (ElastiCache, OpenSearch, MSK), cluster sizing with resharding implications (Redshift), and storage replication (S3, EFS). When the parameter differs from 0.5, affected rows say so in their rationale and the response carries a warning naming the assumption.

### Backup Retention Assumption (Backup & Restore)

The backup-restore multiplier for Amazon RDS - **0.03** - assumes **cross-region automated backups of the data tier**, roughly 3% of cluster compute on a typical cluster, with no compute running in the DR region. Aurora's free backup allowance (equal to the latest cluster volume size) means source-region retention adds nothing; the DR-region cost is the cross-region copy plus incremental transfer.

Snapshot copy mechanics worth knowing:

- **Cross-region snapshot data transfer is incremental.** AWS only ships the changed blocks for each copy, not the full database every time. This applies to both Aurora and standard RDS engines.
- **Retention drives DR-region storage.** Each retained snapshot occupies storage in the DR region, so longer retention or many full copies increases the cost beyond the baseline automated-backup figure.
- **Restore is fully supported.** AWS handles the snapshot chain transparently - restoring from an older snapshot is the same operation as restoring from yesterday's. No manual reassembly.

If your DR plan keeps heavier retention or full daily copies, tune the multiplier upward:

| Backup posture | Approximate RDS multiplier |
|---|---:|
| Cross-region automated backups (~3% of cluster compute) | ~0.03 (current curated value) |
| 7-day daily snapshots retained in DR | ~0.15 |
| 30-day daily full copies retained in DR | ~0.50-0.60 |
| AWS Backup vault with continuous PITR + 30-day retention | ~0.70 |

The curated 0.03 reflects the lightweight cross-region automated-backup posture, which is what a snapshot-only DR plan actually costs - the DR-region backup storage is a small fraction of the running cluster. If your compliance regime requires 30-day full-copy retention, override the multiplier in `known_multipliers.json` - the model is intentionally easy to retune.

## How Multipliers Work

Each AWS service is assigned a multiplier per strategy through one of three service-level resolution tiers, plus an always-on usage-type-group tier described in [Usage-Type-Group Resolution](#usage-type-group-resolution) below:

### Tier 1: Curated (medium-high confidence)

Services with a hand-analyzed multiplier based on their documented DR replication mechanism and charge model. The full curated set covers 20+ services; the table below shows a representative subset, and the complete set with per-strategy rationale for every entry is rendered in [multiplier_reference.md](multiplier_reference.md) (generated from the data files):

| Service | Backup/Restore | Pilot Light | Warm Standby | Active/Active | Key Cost Driver |
|---|---|---|---|---|---|
| Amazon RDS | 0.03 | 0.50 | 0.50 | 1.00 | Backup/restore: cross-region automated backups (~3% of cluster compute), no DR-region compute. Pilot light: one cross-region read replica at the primary instance class (~50% of a writer-plus-reader pair). Warm standby: scaled-down cluster at ~50% of primary writer-plus-reader capacity. Active/active: full second cluster with bidirectional replication. |
| Amazon DynamoDB | 0.02 | 0.55 | 0.55 | 0.55 | Global Tables replicate writes and storage; no scaled-down replica option, so the value is flat across running strategies. Backup/restore is PITR plus cross-region backup copy only. |
| Amazon S3 | 1.00 | 1.00 | 1.00 | 1.00 | CRR duplicates the bucket regardless of strategy - DR roughly doubles S3 cost. Replication transfer and versioning vary with change rate and are absorbed by the directional tolerance. |
| Amazon ECS | 0.00 | 0.00 | 0.50 | 1.00 | Fargate/task capacity; ~50% in warm standby, full capacity in each region for active/active |
| EC2 Instances | 0.00 | 0.00 | 0.50 | 1.00 | Compute hours only when running; ~50% in warm standby, full capacity in each region for active/active |
| EC2-Other (EBS) | 0.00 | 0.00 | 0.50 | 1.00 | EBS tracks the EC2 footprint; no provisioned volumes until compute runs, scaling to 50%/100% with the fleet |
| Elastic Load Balancing | 0.00 | 0.20 | 0.20 | 0.20 | Second load balancer hours in the DR region; LCUs are traffic-driven and not duplicated |
| AWS Lambda | 0.00 | 0.00 | 0.00 | 0.00 | Request-based billing; no cost without invocations |
| Amazon ElastiCache | 0.00 | 0.30 | 0.65 | 0.65 | Global Datastore secondary node hours; ~30% at pilot light, ~65% at warm standby and active/active |
| Amazon Kinesis | 0.00 | 0.25 | 0.25 | 0.00 | Shard-hour charges persist even without ingestion |
| Amazon OpenSearch | 0.025 | 0.30 | 0.70 | 1.00 | Cross-cluster index replication; instance hours at reduced size in standby |
| Amazon Redshift | 0.01 | 0.01 | 0.50 | 0.50 | Snapshot storage and transfer; no cluster compute until warm standby |
| Amazon CloudWatch | 0.05 | 0.70 | 0.75 | 0.75 | Metric and alarm definitions exist in DR; log volume unchanged |
| Amazon VPC | 0.10 | 0.10 | 0.20 | 0.70 | Interface endpoint hours; NAT gateway and bandwidth scale with traffic |
| AWS KMS | 0.80 | 0.80 | 0.80 | 1.00 | Per-key monthly fee plus API calls for replicated data encryption |
| Amazon EFS | 0.20 | 1.00 | 1.00 | 1.00 | Storage mirrored in DR via EFS Replication; backup/restore uses AWS Backup cross-region copy |
| Amazon MSK | 0.05 | 0.65 | 1.00 | 1.90 | Broker capacity plus connector overhead for topic replication |

These are stored in `dr_cost_estimator_mcp/core/data/known_multipliers.json` and can be edited directly.

### Tier 2: Category (medium confidence)

Services that aren't individually analyzed but match a service family by keyword. Six categories are defined:

| Category | Warm Standby Default | Rationale |
|---|---|---|
| Compute (serverless) | 0.00 | Usage-based (Lambda, App Runner); no standby cost until traffic arrives |
| Compute (server/task) | 0.50 | EC2, ECS/EKS, Fargate, Batch run ~50% of primary in warm standby, full capacity per region at active/active |
| Data | 0.80 | Storage replication is proportional to data volume |
| Networking | 0.50 | Load balancers, NAT gateways, and endpoints run ~50% of primary surface |
| Streaming | 0.25 | Provisioned capacity (shards, partitions) even without traffic |
| Management | 0.15 | Small incremental cost for monitoring DR resources |

Category definitions live in `dr_cost_estimator_mcp/core/data/categories.json`.

### Tier 3: Default (low confidence)

Services that match neither a curated entry nor a category keyword get a conservative fallback multiplier (0.25 for warm standby). These are flagged in the tool output so you know which rows to sanity-check.

## The Math

For each service in your cost data:

```
dr_additional_cost = current_monthly_cost × multiplier
total_with_dr = current_monthly_cost + dr_additional_cost
```

Totals are summed across all services, and `dr_as_percent_of_current` gives you the headline number: "DR adds X% to your monthly bill under this strategy."

## Worked Example: Deriving Ratios for a 3-Tier Web Application

This section walks the derivation process end to end for a reference architecture.
The dollar figures are illustrative, not pricing claims: they show how you would derive DR ratios from design assumptions and apply them to your own Cost Explorer data.
The shipped multiplier tables (`known_multipliers.json` and `usage_type_group_multipliers.json`) remain the authoritative defaults the tools apply; [How the Example Relates to the Shipped Defaults](#how-the-example-relates-to-the-shipped-defaults) explains where and why the hand-derived example ratios differ.

### The Reference Architecture

A 3-tier web application with web and app tiers behind load balancers, a relational database, and object storage for static assets:

- Amazon EC2 instances serve the web and app tiers.
- Application Load Balancers handle public and internal balancing across each tier.
- Amazon RDS for PostgreSQL provides the data tier with a writer and a reader.
- Amazon S3 stores static assets.
- Amazon EBS provides persistent block storage for the EC2 instances and the RDS database.

### Baseline Cost by Usage Type Group

The main cost drivers, broken down by usage type group in AWS Cost Explorer, become the baseline for estimating the additional cost of each strategy:

| Usage type group | Description | Monthly cost (USD) |
|---|---|---:|
| EC2-Instances | Compute hours for running EC2 instances (on-demand, spot, or reserved) | $281 |
| EC2-Other | EBS volume storage, provisioned IOPS, throughput, and snapshot storage | $66 |
| ELB: Hours | Fixed hourly charge for each running load balancer | $33 |
| ELB: LCU | Consumption-based charge that scales with connections, bandwidth, and rule evaluations | $17 |
| RDS: Running Hours | Compute hours for running database instances (writer and reader) | $237 |
| RDS: Storage | Allocated database storage, backup storage, and I/O requests | $12 |
| S3 | Object storage, requests, and data retrieval | $38 |
| **Total** | | **$684** |

Per-row DR costs in the tables below are rounded to whole dollars; strategy totals are computed before rounding, so a total can differ from the sum of its rounded rows by a dollar.

### Backup and Restore: $44/month Additional

Choose backup and restore when RPO and RTO are measured in hours.
Data and configuration are backed up to the recovery region, but no infrastructure runs there until you start recovery.
During failover, you provision all required resources and restore the database from a copied snapshot.

| Usage type group | DR design and rationale | Current (USD) | DR ratio | DR cost (USD) |
|---|---|---:|---:|---:|
| EC2-Instances | No EC2 instances are provisioned; the cost of compute in DR is zero until you initiate failover | $281 | 0 | $0 |
| EC2-Other (EBS) | With stateless compute, instances are rebuilt from AMIs and infrastructure as code at recovery time; cross-region AMI snapshot storage adds negligible cost | $66 | 0 | $0 |
| ELB: Hours and LCU | No load balancer runs in DR; you provision at recovery time | $50 | 0 | $0 |
| RDS: Running Hours | No database instances run in DR; you restore from backup at recovery time | $237 | 0 | $0 |
| RDS: Storage | A cross-region snapshot is one database copy, roughly half the primary cost which includes storage for both writer and reader | $12 | 0.5 | $6 |
| S3 | Cross-Region Replication (CRR) copies the full bucket to the DR region | $38 | 1.0 | $38 |
| **Total** | | | | **$44** |

### Pilot Light: $196/month Additional

Choose pilot light for RPO in minutes and RTO in tens of minutes.
The minimal infrastructure needed for a fast recovery runs continuously in the recovery region - primarily live data replication and networking - while compute is provisioned only at failover.
A cross-region RDS read replica and a load balancer run continuously; at failover, you provision the application tier and promote the replica to primary.

| Usage type group | DR design and rationale | Current (USD) | DR ratio | DR cost (USD) |
|---|---|---:|---:|---:|
| EC2-Instances | No EC2 instances are provisioned until failover | $281 | 0 | $0 |
| EC2-Other (EBS) | Instances are rebuilt from AMIs and infrastructure as code at recovery time | $66 | 0 | $0 |
| ELB: Hours | A load balancer runs in DR ready to receive traffic; you pay for hours while running | $33 | 1.0 | $33 |
| ELB: LCU | No active traffic is sent to the DR site until failover | $17 | 0 | $0 |
| RDS: Running Hours | A cross-region read replica runs continuously using the same instance class as the primary (one instance of a writer-plus-reader pair) | $237 | 0.5 | $118 |
| RDS: Storage | The cross-region replica stores one copy of the data volume | $12 | 0.5 | $6 |
| S3 | CRR copies the full bucket to the DR region | $38 | 1.0 | $38 |
| **Total** | | | | **$196** |

### Warm Standby: $369/month Additional

Choose warm standby for RPO in seconds to minutes and RTO in minutes.
You run a scaled-down but fully functional copy of the production environment in the recovery region: web, app, and data tiers all run continuously at reduced capacity.
At failover, you scale out the existing fleet rather than provisioning from scratch, which gives warm standby a lower RTO than pilot light.

| Usage type group | DR design and rationale | Current (USD) | DR ratio | DR cost (USD) |
|---|---|---:|---:|---:|
| EC2-Instances | Half of the primary fleet runs in DR, ready to scale out during failover | $281 | 0.5 | $140 |
| EC2-Other (EBS) | EBS volumes are running for the provisioned EC2 instances | $66 | 0.5 | $33 |
| ELB: Hours | A load balancer runs in DR ready to receive traffic | $33 | 1.0 | $33 |
| ELB: LCU | No active traffic is sent to the DR site until failover | $17 | 0 | $0 |
| RDS: Running Hours | A cross-region read replica runs continuously using the same instance class as the primary | $237 | 0.5 | $118 |
| RDS: Storage | The cross-region replica stores one copy of the data volume | $12 | 0.5 | $6 |
| S3 | CRR copies the full bucket to the DR region | $38 | 1.0 | $38 |
| **Total** | | | | **$369** |

### Active/Active: $676/month Additional

Choose active/active for near-zero RPO and RTO.
Both regions serve traffic simultaneously, with requests routed by geography, latency, or load; web, app, and data tiers run continuously in both regions.
If one region becomes impaired, the other absorbs the traffic.

| Usage type group | DR design and rationale | Current (USD) | DR ratio | DR cost (USD) |
|---|---|---:|---:|---:|
| EC2-Instances | A full fleet runs with capacity scaled to match the primary region | $281 | 1.0 | $281 |
| EC2-Other (EBS) | EBS volumes are running for the full provisioned fleet | $66 | 1.0 | $66 |
| ELB: Hours | A second load balancer runs and receives traffic | $33 | 1.0 | $33 |
| ELB: LCU | The architecture splits active traffic equally across both sites, so total LCU consumption is unchanged but half lands in the second region | $17 | 0.5 | $8 |
| RDS: Running Hours | A separate database cluster runs in each region matching the primary capacity | $237 | 1.0 | $237 |
| RDS: Storage | The second cluster stores a full copy of the data | $12 | 1.0 | $12 |
| S3 | CRR copies the full bucket to the DR region | $38 | 1.0 | $38 |
| **Total** | | | | **$676** |

### Comparing the Strategies

On the $684/month baseline, the estimated DR overhead ranges from about 6% (backup and restore) to about 99% (active/active):

| Strategy | RPO / RTO | Additional DR cost (USD/month) | Typical criticality tier |
|---|---|---:|---|
| Backup and restore | Hours / Hours | $44 | Low |
| Pilot light | Minutes / Tens of minutes | $196 | Medium |
| Warm standby | Seconds to minutes / Minutes | $369 | High |
| Active/active | Near-zero / Near-zero | $676 | Critical |

Across a portfolio, you may have multiple workloads with similar architectures but different business criticality.
A business impact analysis quantifies the cost of downtime and data loss, customer impact, regulatory obligations, and reputational risk; those inputs set the RTO and RPO requirements that drive strategy selection.
Use a comparison like this one to check that each workload's DR strategy and estimated cost are proportional to its business criticality.

### How the Example Relates to the Shipped Defaults

The example above derives ratios by hand for one specific architecture.
The shipped tables in `dr_cost_estimator_mcp/core/data/` are the authoritative defaults the tools actually apply, calibrated for typical portfolios rather than any single design.
Where the two differ, the difference is intentional:

- The example splits Elastic Load Balancing into an always-on Hours line (ratio 1.0 at pilot light and above) and a traffic-driven LCU line. The shipped model makes the same split via the usage-type-group tier (`EC2: ELB - Running Hours` at 1.0 from pilot light up, `EC2: ELB - LCU Running Hours` at 0). One difference: the example charges LCU at 0.5 under active/active, counting the DR-region share of capacity units; because the shipped multipliers measure *additional* cost and total LCU consumption is unchanged when traffic splits, the shipped LCU multiplier is 0 at every strategy. The blended service-level ELB multiplier of 0.20 remains only as the fallback when the per-group split cannot run.
- The example sets RDS Storage to 0.5 because the primary storage line covers both a writer and a reader while DR holds one copy. The shipped usage-type-group `RDS: Storage` multiplier is a more conservative 1.0, assuming DR storage mirrors the full primary storage line.
- The example zeroes RDS Running Hours under backup and restore and carries the residual backup cost on the storage line. The shipped blended RDS `backup_restore` multiplier of 0.03 encodes the same posture (cross-region automated backups, no DR-region compute) expressed as a fraction of total RDS spend.

When the ratios you derive for your own workloads differ from the shipped defaults, override them - see [Extending the Model](#extending-the-model).

## Scoping to a Workload with Cost Allocation Tags

Accounts commonly host multiple workloads, and DR strategy selection happens per workload, not per account. The `tag_filters` parameter scopes the entire estimate to spend carrying your cost allocation tags - `{"Project": ["checkout"]}` estimates DR for the checkout workload only, and `{"Project": ["checkout", "payments"], "Environment": ["prod"]}` means Project IN (checkout, payments) AND Environment = prod. The scope applies to every Cost Explorer query the tools issue, including the per-usage-type-group detail queries, so the compute-vs-storage split stays consistent with the workload boundary. Tag scoping composes freely with `linked_account_ids`: one workload's tags across one account, a list of accounts, or the whole organization.

Two caveats to keep tag-scoped estimates honest:

- **Only tagged spend is counted.** Cost Explorer matches resources that carry the tag; anything untagged is invisible to the estimate, so a workload with incomplete tagging is *underestimated*. Every tag-scoped response carries a warning saying so. Shared/untagged platform costs (networking, security tooling) typically need a separate allocation decision.
- **Tags must be activated.** User-defined tags only appear in Cost Explorer after being activated as cost allocation tags in the Billing console, and activation applies to spend from that point forward (with up to 24 hours of delay). A valid tag that was never activated matches nothing.

## Customizing for Your Portfolio

Within each DR strategy, there may be multiple design options per AWS service that satisfy the same RTO and RPO requirements.
Cloud architects own the DR design assumptions - standby capacity, replication approach, scale-up behavior - and FinOps teams can use those assumptions to refine the ratios for portfolio-level estimates.
Common adjustments:

- **Backup and restore.** The defaults assume native AWS service features for backup (snapshots, Cross-Region Replication). If you use AWS Backup, factor in its pricing model: vault storage, cross-region copy, and restore charges.
- **Pilot light.** The RDS ratio of 0.5 assumes a cluster with one writer and one read replica. A read-heavy database may run more read replicas in the primary region to scale reads; in that case the ratio decreases proportionally (for example, one cross-region replica against a primary with three instances gives a ratio of 1/3).
- **Warm standby.** The defaults assume warm compute capacity scaled to 50% of the primary fleet. The right percentage depends on your RTO requirements: lower warm capacity reduces cost but requires pre-scaling resources before they can receive full traffic during failover.
- **Active/active.** The defaults size each region to immediately absorb the loss of the other (compute ratio 1.0). You can reduce cost by sizing each region at 50% and load balancing across them, but on regional impairment the surviving region must scale up to handle the full load, which increases RTO.

## Operationalizing the Cost Model

- **Agree on an accuracy target with your stakeholders.** For initial budget planning, roughly +/-25% is achievable by customizing ratios for your top services. As individual workloads move into detailed design, refine with workload-specific estimates.
- **Customize the top services first.** Sort your Cost Explorer data by service to identify what drives most of your spend; for most workloads, a small number of services accounts for much of the bill. Work with your cloud architects to derive ratios for those services first - they have the largest impact on the estimate.
- **Use conservative defaults for the tail.** For services that represent a small fraction of spend and don't yet have a derived ratio, apply conservative defaults. The category and default tiers do exactly this, and the dollar-weighted `confidence_summary` shows how much of your spend rests on them.
- **Validate against actual multi-region spend.** If you already have workloads deployed to multiple regions, compare the ratio-based estimate against their actual DR-region spend in Cost Explorer. Use the comparison to calibrate your ratios and identify where your design assumptions differ, and update the ratios as your DR designs mature.
- **Budget separately for implementation and operations.** The ratios capture AWS service cost in the DR region but not the initial DR implementation cost or ongoing operational costs: running a DR program, regular failover testing, and runbook maintenance. For guidance on planning and operating a DR program, see [Plan for Disaster Recovery (DR)](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/plan-for-disaster-recovery-dr.html) in the AWS Well-Architected Reliability Pillar.

## What Gets Excluded

Certain line items are not DR-replicable infrastructure and are automatically filtered out:

- Tax, refunds, credits
- AWS Support charges
- Savings Plans and Reserved Instance fees
- Standalone data-transfer line items (cross-region replication traffic is deliberately out of model - see [Cross-Region Data Transfer Is Out of Model](#cross-region-data-transfer-is-out-of-model))
- Cost Explorer, Budgets, Marketplace

These are defined in `dr_cost_estimator_mcp/core/data/skip_services.json`.

## Confidence and Limitations

The tool reports a dollar-weighted `confidence_summary` on every response showing how much of your spend resolved via each tier. Use this to gauge how much of the estimate is grounded in curated analysis versus category/default fallbacks.

**When the estimate is most accurate:**
- Your spend is dominated by well-understood services (RDS, DynamoDB, S3, ECS, Lambda)
- You're comparing strategies against each other (relative ordering is more stable than absolute numbers)

**When to treat it as rough:**
- Many services hit the default fallback (newer or SaaS-like services such as Amazon Q or Amazon SageMaker)
- Your DR architecture deviates significantly from the assumed pattern (e.g., you use a third-party replication tool instead of native AWS replication)
- You have large Reserved Instance or Savings Plan commitments that wouldn't transfer to the DR region

## Extending the Model

To improve accuracy for your specific workload, add curated multipliers for services that currently fall back to category or default. The DR cost multipliers live in JSON files under `dr_cost_estimator_mcp/core/data/`; edit the files directly in your clone. A generated, human-readable rendering of every shipped value and rationale lives in [multiplier_reference.md](multiplier_reference.md) - after editing the data files, regenerate it with `uv run python scripts/generate_multiplier_reference.py`.

### Adding a New Service

Open `dr_cost_estimator_mcp/core/data/known_multipliers.json` and add an entry. Amazon SageMaker is a good example because it is a judgment call the tool deliberately leaves to you: endpoints are real regional infrastructure, but whether DR duplicates them depends on how critical your inference path is. The values below encode one possible design - snapshot model artifacts to S3 for backup/pilot, run scaled-down endpoints from warm standby up - and yours may differ:

```json
"sagemaker": {
  "backup_restore": 0.02,
  "pilot_light": 0.02,
  "warm_standby": 0.5,
  "active_active": 1.0,
  "explanations": {
    "backup_restore": "Model artifacts replicate to S3 in DR; endpoints are redeployed from IaC at recovery time.",
    "pilot_light": "Same as backup restore: artifacts replicated, no endpoints running until failover.",
    "warm_standby": "Scaled-down inference endpoints (~50% of primary capacity) running in DR.",
    "active_active": "Full endpoint capacity in both regions behind weighted routing."
  }
}
```

Use the lowercase service name as it appears in Cost Explorer.
The server picks up changes on next restart (no rebuild needed when running through `uv run`).

### Data Files

| File | Purpose |
|---|---|
| `known_multipliers.json` | Per-service curated multipliers and explanations |
| `usage_type_group_multipliers.json` | Per-usage-type-group multipliers for RDS, OpenSearch, and ELB (always-on tier), keyed by exact Cost Explorer group strings |
| `categories.json` | Category keywords and fallback multipliers (compute, data, networking, streaming, management) |
| `skip_services.json` | Services excluded from estimates (tax, refunds, support) |
| `defaults.json` | Last-resort fallback multiplier when nothing else matches |

### Keeping Customizations Separate (Optional)

To avoid merge conflicts when pulling upstream updates, set the `DR_MULTIPLIERS_DIR` environment variable in your MCP client config to a directory containing your own `known_multipliers.json` and/or `usage_type_group_multipliers.json`.
Entries in those two files merge on top of the built-in data (your entries win on key conflict; for `usage_type_group_multipliers.json` the merge is per service, so an override service entry replaces that service's whole group table).
Other files (`categories.json`, `skip_services.json`, `defaults.json`) are replaced entirely if present in the override directory.

## S3 Security Best Practices for DR Implementations

When implementing S3-based DR strategies (cross-region replication, versioning), apply the following security controls in both the primary and DR regions:

| Control | Configuration | Priority |
|---|---|---|
| Block Public Access | Enable at account and bucket level | Critical |
| Encryption at rest | SSE-S3 (default) or SSE-KMS for regulated workloads | Critical |
| TLS enforcement | Bucket policy with `aws:SecureTransport` condition | Critical |
| Access logging | Enable server access logging to a dedicated log bucket | High |
| MFA Delete | Enable on versioned buckets storing critical backups | High |
| Least privilege bucket policy | Scope `s3:ReplicateObject` to the replication role ARN only | High |

Example bucket policy enforcing TLS:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::YOUR-DR-BUCKET",
        "arn:aws:s3:::YOUR-DR-BUCKET/*"
      ],
      "Condition": {
        "Bool": { "aws:SecureTransport": "false" }
      }
    }
  ]
}
```

The cost multipliers in this tool account for storage and replication charges but do not include costs for AWS KMS keys or additional logging storage. Factor those into your budget separately if applicable.

## Confidence Tiers

Each resolution tier maps to a confidence level reported on the row and aggregated into the dollar-weighted portfolio `confidence_summary`:

| Resolution source string | Confidence |
|---|---|
| `usage_type_group:<group>` | `high` |
| `known` | `medium-high` |
| `category:<name>` | `medium` |
| `default` | `low` |

The `high` band is reserved for usage-type-group matches because those resolve to the most specific guidance available: a per-cost-component multiplier rather than a blended service-level value.
Curated service-level matches are reported as `medium-high` so the component-vs-blended distinction stays visible in the summary.
A usage-type-group-split service row reports the worst confidence among its constituent groups, so a service whose remainder slice resolved via the blended multiplier reports `medium-high` rather than `high`.

The dollar-weighted portfolio summary reports the percentage of total cost resolved at each tier (`percent_cost_high_confidence`, `percent_cost_medium_high_confidence`, `percent_cost_medium_confidence`, `percent_cost_low_confidence`) plus the count of distinct line items at each tier (`line_item_count_by_confidence`).

## Usage-Type-Group Resolution

The **usage-type-group tier** is an always-on resolution layer that sits above the service-level tiers, making the full resolution order:

```
usage_type_group → service → category → default
```

This tier splits supported services by their Cost Explorer `USAGE_TYPE_GROUP` dimension - separating, for example, compute hours from storage and load-balancer hours from capacity units - and applies a per-group multiplier instead of a single blended service-level value. The result is a DR estimate that reflects per-cost-component economics for services whose cost drivers behave very differently under DR.

### Why This Matters

A blended multiplier treats every dollar of Amazon RDS spend the same. In reality, backup/restore DR needs zero running instances (compute = 0×) but a full copy of storage (storage = 1×). A blended 0.03 multiplier averages these together based on typical workload proportions. When your workload deviates from that average - for example, a storage-heavy RDS deployment with small instances - the blended estimate drifts. Usage-type-group resolution removes that drift by multiplying each cost component independently.

### Supported Services and Groups

Three services currently ship usage-type-group multipliers. The group keys are the exact `USAGE_TYPE_GROUP` dimension values Cost Explorer reports, verified against the live API - filters match verbatim strings, so the spelling matters:

- **Amazon Relational Database Service** - `RDS: Running Hours`, `RDS: Storage`, `Aurora: Backup Usage`, `RDS: I/O Requests`
- **Amazon OpenSearch Service** - `OpenSearchService: Running Hours`
- **Amazon Elastic Load Balancing** - `EC2: ELB - Running Hours`, `EC2: ELB - LCU Running Hours`

All other services resolve at the service, category, or default tier. Amazon Redshift intentionally has no usage-type-group entries: its live-reported groups do not separate compute from storage in a way that adds signal over the blended service-level multiplier.

### How It Works

The Cost Explorer API accepts `USAGE_TYPE_GROUP` only as a *filter*, never as a `GroupBy` dimension, so the split works by issuing one filtered query per configured group. On every `compare_dr_strategies` or `analyze_dr_strategy` call:

1. The tool issues an initial `GetCostAndUsage` grouped by `SERVICE` to discover all services with non-zero spend.
2. For each supported service with spend, the tool issues one `GetCostAndUsage` per configured group, filtered to `SERVICE` and that exact `USAGE_TYPE_GROUP` value.
3. Each matched group resolves against its per-group multiplier.
4. Spend inside the service that no configured group captured becomes a synthetic **remainder** row (`(other usage)`) that resolves via the blended service-level multiplier, so the service total is preserved exactly. In practice the remainder catches line items like RDS Extended Support fees, RDS Proxy, or OpenSearch Serverless OCUs.
5. The per-group results are re-aggregated back to service-level rows so the response shape stays the same.
6. Unsupported services use the blended multiplier path without any extra API calls.

If a supported service's group queries fail or no configured group has spend, the tool falls back to the blended service-level multiplier for that service and appends a warning. Each configured group adds one `GetCostAndUsage` call per supported service with spend (at most seven extra calls with the shipped table).

### Default Multiplier Values

The table below shows the per-usage-type-group multipliers for each supported service and DR strategy. These values are stored in `dr_cost_estimator_mcp/core/data/usage_type_group_multipliers.json`.

#### Amazon Relational Database Service

| Usage Type Group | Backup/Restore | Pilot Light | Warm Standby | Active/Active | Rationale |
|---|---:|---:|---:|---:|---|
| RDS: Running Hours | 0.00 | 0.50 | 0.50 | 1.00 | No instances run in DR for backup/restore. Pilot light and warm standby run a cross-region read replica (~50% of a writer+reader pair). Active/active runs full compute. |
| RDS: Storage | 1.00 | 1.00 | 1.00 | 1.00 | Storage replicates fully in all strategies that maintain a DR copy. |
| Aurora: Backup Usage | 1.00 | 1.00 | 1.00 | 1.00 | Backup retention in the DR region mirrors the primary. |
| RDS: I/O Requests | 0.00 | 0.50 | 0.50 | 1.00 | I/O tracks compute - no running instance means no I/O charges. |

RDS data-transfer groups deliberately carry no multiplier (see [Cross-Region Data Transfer Is Out of Model](#cross-region-data-transfer-is-out-of-model)); transfer spend lands in the remainder row at the blended multiplier.

#### Amazon OpenSearch Service

| Usage Type Group | Backup/Restore | Pilot Light | Warm Standby | Active/Active | Rationale |
|---|---:|---:|---:|---:|---|
| OpenSearchService: Running Hours | 0.00 | 0.30 | 0.70 | 1.00 | No cluster in DR for backup/restore. Pilot light runs a minimal cross-cluster replication follower. Warm standby runs a follower at ~70% of primary capacity. |

#### Amazon Elastic Load Balancing

| Usage Type Group | Backup/Restore | Pilot Light | Warm Standby | Active/Active | Rationale |
|---|---:|---:|---:|---:|---|
| EC2: ELB - Running Hours | 0.00 | 1.00 | 1.00 | 1.00 | A second load balancer runs in DR from pilot light up, ready to receive traffic; the hours line doubles. |
| EC2: ELB - LCU Running Hours | 0.00 | 0.00 | 0.00 | 0.00 | Capacity units are traffic-driven. Standby postures serve no traffic; at active/active, traffic splits across regions without increasing total LCU consumption, so the additional cost is zero. |

### Confidence

Rows resolved via the usage-type-group table are reported at **high** confidence (source string `usage_type_group:<group>`). The remainder row resolves via the blended service-level multiplier at `medium-high` confidence, and the re-aggregated service row reports the worst confidence among its constituents - so a service with a non-trivial remainder shows `medium-high`, keeping the blended slice visible.

### Always-On Behavior

Usage-type-group resolution is always on for both live Cost Explorer tools (`compare_dr_strategies` and `analyze_dr_strategy`); there is no parameter to enable or disable it.
Supported services (RDS, OpenSearch, ELB) are always split by usage type group; all other services resolve through the blended service-level tiers.
The only behavioral difference versus a purely blended run is the extra per-group `GetCostAndUsage` queries for each supported service - the response shape is identical.

### Adding Groups for Your Workload

To split additional services (or capture spend currently landing in a remainder row), discover the exact group strings your account reports:

```bash
aws ce get-dimension-values \
  --time-period Start=2025-01-01,End=2025-07-01 \
  --dimension USAGE_TYPE_GROUP --region us-east-1
```

Then add entries to `usage_type_group_multipliers.json` (directly or via the `DR_MULTIPLIERS_DIR` override) keyed by the service display name and the verbatim group string. A misspelled group is harmless but useless: the filter matches nothing and the spend stays in the remainder.
