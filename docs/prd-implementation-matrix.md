# PRD v0.9 — implementation and acceptance ledger

This is a completion ledger, not a claim of full production acceptance.

| Area | Implemented | Evidence / outstanding gate |
|---|---|---|
| Login | Password, roles, disabled users, reset/revoke sessions, Entra OIDC flow, preauthorized email + immutable tenant/object binding | Password and access tests pass. Real Entra application/consent/callback configuration required for SSO acceptance. |
| Customer | CRUD, versioned discounts, optional one-transaction onboarding with 1–3 Tenant/SP groups and queued verification | Onboarding rollback/conflict and async job tests. Basic create remains for members without credential privileges. |
| Tenant / SP | Encrypted credentials, rotation queues verification, distinct AUTH / DISCOVER / COST / SYNC states | 2026-11-10: real Auth and discovery succeeded for the saved tenant; all three discovered subscriptions proved Cost-readable. Successful Auth alone never implies Cost permission. |
| Subscription | Auto-discovery, manual creation, missing-not-deleted, stop/resume, historical transfers | Ownership-date calculations and conflict tests pass. |
| Cost ingestion | Standard CSV + explicit modern/legacy Azure CSV mappings, API ActualCost Query adapter, strict Export/Manifest read adapter | CSV/API fixtures and real API ingestion tested (including high-precision source costs); genuine CSP price base and two real Export Runs/raw CSV samples remain required. Unknown schemas reject rather than guess. |
| Cost versioning | Immutable raw batches, idempotent digest, atomic day publication, source coverage guard, partition locks | Repeat import, partial coverage rejection, corrected day and ownership tests pass. |
| Worker | Durable database queue, leader lock, priorities, partition checkpoints, cancellation, 3 retries after first attempt, 7-day daily lookback, supplemental refresh at most every 6 hours, schedule idempotence, restart recovery | Fixture worker tests; VM runs an independent worker. Actual 12-hour SLA requires upstream-ready timing evidence at representative production throughput. |
| Dashboard | Object/date filters, exact Decimal/NUMERIC ACR, lifetime, daily/monthly trend, comparable-month change, Top5, source timestamps and completeness | SQLite/PostgreSQL accounting regressions. First-query and warmed-cache latency are reported separately. Customer/tenant/subscription table searches use stable server pagination. Native-currency source totals, lifetime, trends, Top5 and services remain visible before price/FX verification; missing dates stay null and source costs never become verified ACR automatically. Separately labeled provisional ACR previews assume a pre-discount source and may use the explicitly approved current-discount fallback for missing history; verified-only switching, alerts and standard reports remain separate. |
| Alerts | Two-final-daily-failure rule, >50% AND >$100 spike, complete zero-day rule, dedup, read/ack/mute/close/recover | Threshold/zero/missing-data fixtures. Real Email/Teams delivery requires channel configuration; channels default disabled. |
| Settings | Users, SMTP STARTTLS and Teams secrets encrypted/redacted, SSO config, schedule, threshold defaults/customer overrides, manual FX, audit | Role/secret-redaction/UI tests. Retention/security policy still needs business/operations sign-off. |
| Reports | Background streaming XLSX/CSV (PostgreSQL server cursor; write-only Excel with sheet rollover), customer/tenant/subscription/service, day/month, date/object scope, discount/FX versions, quality status, owner-only downloads | Worker exports and browser download-ready test pass. |
| Delete / restore | Preview + actor-bound expiring challenge + typed ID, scoped cascade, export invalidation, fsynced ledger outside PostgreSQL, replay before serving restored data | SQLite and PostgreSQL old-database restore tests pass. Production purge remains disabled until independent latest-ledger retention and full VM recovery are approved and exercised. Selective historical erasure preserves the current owner and permanently excludes erased dates from reimport; regression covered. |
| Deployment | Docker Compose API/worker/PostgreSQL; Caddy TLS; existing live data retained; pre-upgrade DB/source backups and rollback image | VM staging acceptance runs before activation. |
| Scale | Synthetic 1,002 customers / 10,020 subscriptions / 160,320 facts / 20 concurrent requests; precise SQL aggregation + transactionally invalidated dashboard cache | Workload reports declare 8 days and a 70/30 warmed-cache mix. This does not establish arbitrary 12-month workloads, CSV/Blob throughput, UI-wide performance or 12-hour ingestion SLA. |

## Supported CSV contracts

1. `subscription_id,usage_date,service,currency,amount`
2. `SubscriptionId,Date,ServiceName,BillingCurrencyCode,CostInBillingCurrency`
3. `SubscriptionId,UsageDateTime,MeterCategory,Currency,PreTaxCost`

Azure CSVs may include additional columns. Required mappings must match and dates must parse explicitly. Real customer schemas still need PoC confirmation.

## Export manifest gate

The strict Blob adapter requires `runInfo.startDate`, `runInfo.endDate`, an ordered timezone-aware `runInfo.executionTime`, and `blobs[].blobName/byteCount`. Every shard must be readable and its length must match. A GUID run ID is not used as a chronological ordering. Missing explicit day rows do not become synthetic zeros. Unsupported real manifests fail with an actionable schema error until a validated mapping is added.

## Remaining work before calling the complete PRD accepted

- Real CSP Scope/price reconciliation, per-subscription SP permission proof and actual CSV/Export samples.
- Real SSO, SMTP and Teams configuration and successful delivery/identity testing.
- Full production mixed workload / ingest throughput and upstream availability timing evidence.
- Independent retained deletion ledger, full VM recovery drill, approved backup/audit retention.
- UI catalogs use stable server pagination; selectors still load metadata into memory. Large-scale browser selector behavior needs separate UI acceptance.

No Funding, Claim or official Microsoft ACR entitlement is implemented or inferred.
