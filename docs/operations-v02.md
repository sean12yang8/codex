# AzureLens v0.2 operations

## Automatic tenant onboarding

Saving new tenant SP credentials or rotating them queues a persisted verification task. The worker obtains a tenant-scoped application token, enumerates authorized subscriptions, probes Actual Cost by subscription and imports the most recent seven days, then queues one low-priority 365-day backfill per new subscription. Newest monthly partitions run first. Auth, discovery, Cost read and sync statuses are independent.

On upgrading the existing VM, saved active tenant credentials are preserved and queued once. The initial daily schedule is 02:00 UTC (10:00 Asia/Shanghai); it refreshes the previous seven completed UTC dates to cover late-arriving costs. A supplemental availability refresh runs every six hours by default (configurable from one to six hours), independently from the daily-failure alert counter. Settings can change or disable the schedule. Overlapping data partitions publish under subscription database locks; daily priority precedes backfill at partition boundaries.

## Required Azure permissions

The SP must be granted the appropriate customer subscription discovery/read and Cost Management read access in Azure. CSP billing policies can still deny Cost access despite successful identity authentication. The platform does not grant customer Azure RBAC.

For configured Export Blob storage, grant the saved SP read access to the prepared account/container (normally Storage Blob Data Reader). The platform never creates customer Export or Storage resources. Manifest fields must satisfy the documented supported contract before that source can publish data.

## Cost calculation gate

Use Azure 接入 → 成本口径 to record verified evidence that the raw source is a pre-customer-discount basis. No assumption is made from an API response alone. Unverified records are retained but excluded from verified ACR, with missing FX and unknown/partial coverage kept distinct from zero.

## SSO and notifications

Use 系统设置 → Entra SSO. The registered application needs the exact HTTPS redirect `/api/v1/auth/sso/callback`. Administrators pre-create users; matching email is bound to immutable Entra tenant/object IDs on first authorized login. No public signup exists.

Configure SMTP STARTTLS and Email recipients or a supported Teams HTTPS Webhook in 系统设置. Secrets are encrypted and never echoed. Delivery attempts and failures are visible separately from alert processing. No external channel is enabled by default.

## Backups and restoration

`scripts/backup.sh` creates a protected PostgreSQL dump and a protected encryption-key/ledger archive with SHA-256 manifest. Retain these in access-controlled storage separate from the VM.

`scripts/restore.sh` requires explicit destination confirmation and the **latest independent deletion ledger**. It never overwrites the latest ledger with an old one from the backup. Application startup replays tombstones before serving data. Retain the correct encryption key; replacing it blindly makes old SP credentials undecryptable.

Production permanent deletion defaults off (`PURGE_ENABLED` absent). Do not enable it until the independent ledger and complete VM recovery path have been approved and exercised. Database-only replay has automated coverage; that is not a full VM restore certification.

## Rollback

The prior database/source backup is under `/opt/azurelens/backups/*`; prior runtime images are tagged `azurelens:rollback-v01`, `azurelens:rollback-v02-r1` and subsequent revision tags. Schema additions are additive. Do not restore an old database or ledger over current business data without following the restore protocol and preserving new changes. Upgrade logs are in `/var/log/azurelens-upgrade-v02.log`.

## Report resource use

PostgreSQL reports aggregate exact NUMERIC amounts on the server and stream results in bounded batches. CSV is written row by row; Excel uses write-only worksheets and starts a new sheet after the Excel row limit. Discount/FX effective dates, source batch timestamp, quality status and generation timestamp are included. Workers recover interrupted exports to QUEUED, and high-priority due ingestion jobs precede starting an export. This bounds Python report memory; exceptionally long reports still share the single worker and need throughput acceptance before expanding production scale.
