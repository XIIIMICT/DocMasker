# Runbook — PostgreSQL backup and restore

Canonical home for the backup contract, restore procedure, drill cadence,
off-host configuration, monitoring contract and alert response. Inventory and
volume ownership stay in `docs/OPERATIONS.md` §3; the compose/volume invariants
live in `infra/AGENTS.md`; the monitoring architecture in
`docs/ARCHITECTURE.md` («Observability / monitoring stack»).

Scope: **logical** backups of the application database (`docmasker` by default)
only. Roles, tablespaces, server configuration and WAL are out of scope. The
private artifact volume `dm-jobs-data` is a separate, equally required recovery
point — see «Artifact consistency» below.

## Decision and evidence

- Local and CI PostgreSQL are `postgres:18-alpine`; the authoritative job state
  lives on the named volume `dm-postgres-data` (`infra/compose.db.yaml`). The
  artifact spool is a separate recovery point. RPO/RTO below are assumptions,
  not accepted SLOs; the target-host rehearsal remains OPEN.
- **Chosen: `pg_dump -Fc` custom-format sidecar (`dm-pgbackup` in
  `infra/backup/`), not WAL-G / pgBackRest.** WAL-based tools need server-side
  `archive_mode` / `archive_command` and a separate repository; the external
  production server is not configured by this repository, no server-side
  archive access is available here, and no point-in-time-recovery requirement
  is documented. At one logical database with a daily RPO, a verified full
  dump is adequate and can be delivered entirely in-repo.
- Client compatibility: the sidecar image is `postgres:18-alpine`, so `pg_dump`
  18 can dump both the local 18 stand and an older external server. The reverse
  (a 14 client against an 18 server) is not supported — hence no generic
  `postgresql-client` image.
- **Escalation criteria (reopen the decision):** RPO < 24 h or a PITR/flashback
  requirement; server-side WAL archive or replication-slot access becoming
  available; more than one logical database, or roles/tablespaces that must be
  recovered; the artifact spool must be captured automatically rather than by
  the documented quiesce/copy procedure. If any of these appear, add WAL
  archiving plus a WAL-G/pgBackRest repository (or a spool snapshotter) — do
  not claim this sidecar provides PITR or artifact recovery.

## Schedule, retention and RPO

| Contract | Value | Source |
|---|---|---|
| RPO (assumed, **not** an accepted SLO) | **≤ 24 h** — no stricter need is documented | this runbook, `TODO.md` |
| RTO | **not asserted** — depends on the host and dump size, not measured | this runbook |
| Full backup | daily, `BACKUP_HOUR_UTC=2` (UTC) | `.env.example`, compose |
| Local retention | newest `BACKUP_RETENTION_COUNT=7` dumps in `dm-postgres-backups` | `infra/compose.db.yaml` |
| Restore drill | weekly, Sunday (`BACKUP_DRILL_WEEKDAY=6`) at `BACKUP_DRILL_HOUR_UTC=3` | compose |
| Off-host copy | **OPTIONAL and off by default** — see below | `BACKUP_OFFHOST_TARGET` |

Dumps are named `dm-full-<YYYYmmddTHHMMSSZ>.dump` with a
`dm-full-<…>.fingerprint` sidecar. The dump is written as `.part`, verified,
and only then atomically renamed; a truncated dump is never counted or
restored. Pruning removes the oldest dumps **and** their sidecars after the new
dump is verified. A missed window (daemon started late) is caught up on the
first pass after the target hour; a failed run is **not** silently retried the
same day — it alerts and is retried with `just backup-now`.

## What the drill actually proves

`just backup-drill` restores the newest dump into a temporary database
`dm_backup_drill_<UTCstamp>` and asserts both DDL and data. The assertion is a
fingerprint over opaque ids only — no PII, keys or entity values:

```sql
SELECT CASE WHEN to_regclass('"public".alembic_version') IS NULL THEN ''
       ELSE (SELECT md5(coalesce(string_agg(version_num, ',' ORDER BY version_num), ''))
             FROM "public".alembic_version) END || '|' ||
       CASE WHEN to_regclass('"public".jobs') IS NULL THEN ''
       ELSE (SELECT count(*)::text || '|' ||
             md5(coalesce(string_agg(id::text, ',' ORDER BY id), ''))
             FROM "public".jobs) END;
```

The expected fingerprint is captured **from a scratch restore of the dump
itself** during `backup` (so it describes dump bytes, not a racy live database)
and stored in the `.fingerprint` sidecar. The drill recomputes it after restore
and only counts success when the two strings are equal. The scratch database is
dropped in a `finally` block on both success and failure. The backup role
therefore needs `CREATEDB` (the local `dm` superuser has it); an external role
without it must be granted the right or the sidecar can neither verify nor drill.

## Artifact consistency (required, manual)

The sidecar dumps **only PostgreSQL**. A dump without its referenced artifacts
is not a recovery point. For a real recovery, capture `dm-jobs-data` in the same
quiesced window as the dump (the manual procedure in `docs/OPERATIONS.md` §3
remains the artifact-side procedure): stop writers, dump, copy the spool, resume.
The sidecar's daily dump is a logical snapshot; it does not quiesce the app, so
artifact copies must be scheduled by the operator if a consistent artifact
recovery point is required. Valkey needs no backup (disposable cache index).

## Restore procedure

1. Announce a maintenance window and stop the application workers so no writes
   land during the restore. Keep the original dump and the original database
   until the restored copy has been verified.
2. List the dumps (the sidecar's own volume is the source of truth):

   ```sh
   docker compose run --rm --no-deps --entrypoint sh pgbackup \
     -c 'ls -lh /var/backups/docmasker'
   ```

3. Rehearse first with `just backup-drill`: it performs the exact restore +
   fingerprint assertion + cleanup sequence against the newest dump.
4. For a real recovery create a target database (example `docmasker_restore`)
   and restore into it with the same tools the sidecar uses:

   ```sh
   docker compose run --rm --entrypoint sh pgbackup -c \
     'PGPASSWORD="$POSTGRES_PASSWORD" createdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" docmasker_restore && \
      PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --no-owner --no-privileges --exit-on-error \
        -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d docmasker_restore \
        /var/backups/docmasker/<dump-file>'
   ```

5. Run the fingerprint query above against `docmasker_restore` with
   `psql -v ON_ERROR_STOP=1` and compare it with the `.fingerprint` sidecar.
   Restore the matching artifact spool copy and check that every
   `input_artifact` / `keys_artifact` / `result_artifact` reference resolves.
6. Point the application at the restored database (`POSTGRES_DB`, same
   `POSTGRES_HOST`), start the stack (`just prod` / `just dev`) and verify
   `GET /health`, `GET /ready` and one round-trip job. Run `just migrate` only
   if the dump predates the current Alembic head; applied migrations are
   immutable (`docs/WORKFLOW.md`). Alembic owns `alembic_version` — never edit
   or delete it; a dump older than head is advanced by migrating, not by
   rewriting the version row.

Never restore over the live database in place: restore to a new database,
verify, then switch the connection.

## External / production database caveat

Inside compose the sidecar uses `POSTGRES_HOST=${POSTGRES_HOST_DOCKER:-postgres}`
and container port **5432**. `POSTGRES_PORT` in `.env` is the host-side loopback
publish (15433) for `just seed`/migrations and is **not** the sidecar's target
port. For an external/production database set `POSTGRES_HOST_DOCKER` to its
reachable address; credentials come from the same `POSTGRES_*` values in `.env`
(no new secrets, none hardcoded). The external role needs `CREATEDB` for the
restore-based verification and weekly drill, and network reachability from the
backup container. No external server was contacted by this change: only the
disposable local rehearsal below was executed.

## Maintenance window: one-off Docker migration

Schema changes use the one-off `just migrate-docker` recipe from the **exact
deployed image** (`DM_BACK_IMAGE` tag/digest, never `latest`); exact commands,
preflight and the `0001/0002` gate are in `docs/OPERATIONS.md` §2.2. The
preflight echoes target host/port/database/schema, never a password, and
refuses an ambiguous local target without `ALLOW_LOCAL_DB=1`. Plan the window
as for a restore:

1. Announce the window, stop writers (`docker compose --profile prod stop
   worker back`) and record `just migrate-docker-current`.
2. Take the pre-window **logical dump** (`just backup-now`, or the scheduled
   dump verified by `just backup-drill`) **and** a copy of `dm-jobs-data` in the
   same quiesced window. Migrations change PostgreSQL only; `dm-jobs-data`
   (staged inputs, keys, results) is **not** covered by Alembic and is not
   mounted into the one-off container. Without both, there is no recovery
   point (`docs/OPERATIONS.md` §3, "Artifact consistency" above).
3. Run exactly one migration container to `head` (currently
   `0002_staging_recovery`), verify `alembic current` equals head and that
   `jobs.staging_until` exists, then restart `back`/`worker`/`front` with
   `--no-build`. Do not delete rows or run destructive cleanup: the maintainer
   owns production data, including any 0001→0002 decision.
4. **Rollback:** do not blindly `alembic downgrade -1` (0001 drops `jobs`).
   Restore the pre-window dump into a new database (steps 3–6 of "Restore
   procedure" above), restore the matching artifact copy, redeploy the
   **previous image tag** as `DM_BACK_IMAGE`, then verify
   `just migrate-docker-current` and a round-trip job. Keep the original
   database and dump until verification passes.

For an external DB, set `POSTGRES_HOST_DOCKER` to a container-reachable host or
export an explicit `DATABASE_URL` (the same escape hatch as the sidecar
above); no new credentials are introduced. CI exercises this only against a
throwaway schema in a disposable Postgres service — no shared or production
database is contacted.

## Drill cadence and trigger

- Automatic: weekly as in the table above.
- Manual: `just backup-drill` after any change to the sidecar, the restore
  procedure, the `POSTGRES_SCHEMA` value or a PostgreSQL major upgrade, and
  before a maintenance window that depends on a rollback.

## Off-host copy (OPEN by default)

`BACKUP_OFFHOST_TARGET` is optional. It must be a whitespace-separated command
containing exactly one `{file}` token; the sidecar substitutes the absolute
dump path and runs it **without a shell** (`shlex.split` +
`subprocess.run(shell=False)`):

```dotenv
BACKUP_OFFHOST_TARGET=aws s3 cp {file} s3://bucket/docmasker/pg/
BACKUP_OFFHOST_TARGET=rsync -a {file} backup-host:/srv/docmasker/pg/
```

Credentials come from the operator's environment, an IAM role or an ssh agent —
**no credential is stored in this repository** and no S3/MinIO/rsync
infrastructure is created by this change. When the variable is empty (the
default) the off-host copy is skipped entirely,
`dm_pg_backup_offhost_configured` is `0` and **the off-host requirement is NOT
satisfied**. There is no alert that claims it is satisfied; the operator must
configure and verify a destination. The local drill does not exercise any
off-host target.

## Monitoring contract

The sidecar exposes a narrow stdlib Prometheus endpoint on `pgbackup:9113` (no
host publication). Prometheus scrapes it as job `docmasker-pg-backup`
(`infra/prometheus/prometheus.yml.tpl`); for external mode override
`BACKUP_METRICS_HOST`/`BACKUP_METRICS_PORT` like the other scrape targets.

| Metric | Type / labels | Meaning |
|---|---|---|
| `dm_pg_backup_last_success_age_seconds` | gauge | seconds since last verified dump, computed at scrape time |
| `dm_pg_backup_last_failure_timestamp_seconds` | gauge `{operation="dump\|verify\|prune\|offhost\|drill"}` | last failure per operation |
| `dm_pg_backup_runs_total` | counter `{outcome="success\|failure"}` | backup runs |
| `dm_pg_backup_last_duration_seconds` | gauge `{operation=…}` | last operation duration |
| `dm_pg_backup_last_size_bytes` | gauge | newest dump size |
| `dm_pg_backup_files_retained` | gauge | dumps kept locally |
| `dm_pg_backup_offhost_configured` | gauge | `1` when a target is configured, `0` otherwise |
| `dm_pg_backup_drill_last_success_age_seconds` | gauge | seconds since last successful drill |
| `dm_pg_backup_drill_last_failure_age_seconds` | gauge | seconds since last failed drill (1-year sentinel if never) |
| `dm_pg_backup_drill_runs_total` | counter `{outcome="success\|failure"}` | drills |

All series additionally carry the scrape labels `job=docmasker-pg-backup` and
`env=<LOG_ENV>`. Alerts (group `docmasker_pg_backup` in
`infra/prometheus/alert-rules.yml`):

| Alert | Condition | Severity |
|---|---|---|
| `DocMaskerPgBackupExporterDown` | `up{job="docmasker-pg-backup"} == 0` for 10m | warning |
| `DocMaskerPgBackupFailed` | failure counter increase in 6h | warning |
| `DocMaskerPgBackupOverdue` | last success age > 90 000 s (25 h) | critical |
| `DocMaskerPgRestoreDrillFailed` | last drill failure age < 24 h | warning |
| `DocMaskerPgRestoreDrillOverdue` | last successful drill age > 691 200 s (8 d) | warning |

The provisioned dashboard **«DocMasker — Резервные копии PostgreSQL»**
(`infra/grafana/provisioning/dashboards-json/pg-backup.json`, folder
`DocMasker`) shows backup age/size/retention, off-host state, drill ages and
run outcomes. Edit the JSON, not the browser (`allowUiUpdates: false`). Alerts,
dashboard and this contract change together.

## Alert response

| Alert | First action | Escalation |
|---|---|---|
| `DocMaskerPgBackupExporterDown` | `docker compose ps pgbackup`, `just backup-logs`; check `net` and the healthcheck | recreate the sidecar; verify the scrape target in Prometheus |
| `DocMaskerPgBackupFailed` | read `dm_pg_backup_last_failure_timestamp_seconds{operation=…}` and `just backup-logs` for the failing tool/rc | fix DB connectivity, disk or off-host command, then `just backup-now` |
| `DocMaskerPgBackupOverdue` | treat as RPO breach: `just backup-now`, confirm `dm_pg_backup_files_retained` grows | notify the operator; schedule a manual drill and record the gap |
| `DocMaskerPgRestoreDrillFailed` | run `just backup-drill` and read the `pg_restore`/fingerprint error | a dump may be unrestorable: do not rely on it; verify an older dump and escalate |
| `DocMaskerPgRestoreDrillOverdue` | confirm the weekly window and `CREATEDB` rights, then `just backup-drill` | if the role cannot create scratch DBs, grant `CREATEDB` to the backup role |

## Exit codes

`0` — success. `1` — backup/drill failure without a tool code (no dump, missing
sidecar, fingerprint mismatch, scratch cleanup failure). `2` — configuration
error (bad identifier, missing `{file}` token, bad integer range, missing DB or
password). Any other non-zero value is the failing `pg_dump` / `pg_restore` /
`psql` / `createdb` / `dropdb` / off-host command's own return code. One-shot
modes (`just backup-now`, `just backup-drill`) propagate it; the daemon records
the same information in the metrics above instead of exiting.

## Still OPEN

- Off-host destination: none configured and none exercised
  (`BACKUP_OFFHOST_TARGET` is empty by default). No target was contacted.
- Target-host rehearsal with the real RO-mounted private config and named
  volumes; only the disposable local rehearsal exists (`docs/OPERATIONS.md` §3).
- RTO and any stricter RPO need an operator decision; 24 h is an assumption.
- PITR/WAL archiving and automatic artifact-spool snapshots are not implemented.
- Alert delivery to the operator mailbox was not re-verified here (the SMTP
  relay path is unchanged; see `docs/OPERATIONS.md` §8).
