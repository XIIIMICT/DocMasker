# Production readiness — open target-host gates

This file is the canonical checklist of **gates an operator must sign off on the
authorized host**. Everything already implemented is in the code and its git
history; durable decisions live in `docs/adr/`, and genuinely deferred work in
`TODO.md`. Local disposable drills are evidence that a procedure works, not a
discharge of any row below.

The commands are the same as `docs/TESTING.md` §5; repeat them on the target
host with that host's own `.env` and read-only-mounted private config. Backup,
off-host copy and migration-window details are in `docs/RUNBOOKS.md`.

| # | Step | Verification | Status |
|---|---|---|---|
| 1 | Clean install | `cp .env.example .env` with host values; `just deps` green; no credentials or real exception values in git | **OPEN — operator only** |
| 2 | Private config mount | `back/config/masking/exceptions.yaml` present, RO-mounted into `back`/`worker`; only its digest is recorded, never its values | **OPEN — operator only** |
| 3 | Migration from empty DB | `just db-stand` → `just seed` (applies Alembic to head); deployments use `just migrate-docker` / `just migrate-docker-current` with `DM_BACK_IMAGE` pinned to the deployed image. Verify `alembic current` at head, the `jobs` table and `jobs.staging_until` (`docs/OPERATIONS.md` §2) | **OPEN — operator only** |
| 4 | Prod image build | `docker compose -f infra/compose.ci.yaml build`; images `dm-back:ci` / `dm-front:ci` (or host equivalents) built from the exact commit | **OPEN — operator only** |
| 5 | Compose health | `up -d --no-build --wait --wait-timeout 180 back worker front`; `/health` 200, `GET /ready` 200 after migration; private volumes only | **OPEN — operator only** |
| 6 | E2E | `E2E_BASE_URL=… bun run --cwd front test:e2e --project=smoke`; exact spec discovery and all smoke tests pass | **OPEN — operator only** |
| 7 | Real POSIX SIGTERM | `docker compose stop worker` / `kill -TERM 1` mid-job; in-flight job drains, no stale publication, no second claim | **OPEN — operator only** |
| 8 | Real disk-full and pool pressure | Fill a disposable artifact volume, saturate the real DB pool; bounded errors, no partial output, recovery verified | **OPEN — operator only** |
| 9 | Backup/restore on target | Sidecar `just backup-stand` / `just backup-drill` (verified dump + fingerprint assertion) and quiesce → artifact copy → config backup → restore → verify rows, artifact SHA-256s and config digest; then `GET /ready` and one round-trip job. Off-host `BACKUP_OFFHOST_TARGET` also needs a configured, verified destination | **OPEN — operator only** |
| 10 | Concurrency on target hardware | 1/2/N representative documents; record wall/CPU/RSS/queue wait/DB pool wait and artifact integrity; calibrate any SLO from measurements, never from a target invented here. The 1 GiB worker-container budget behind `JOBS_CONCURRENCY=4` is still unvalidated on Linux (`docs/PERFORMANCE.md`) | **OPEN — operator only** |
| 11 | GitHub-side required check | Branch ruleset + required check `CI required`; repository can only document it | **OPEN — maintainer/GitHub** |
| 12 | Migration rehearsal on the target host | Run the pinned-image one-off against the deployed schema with the real backup and artifact copy before any production DDL (`docs/OPERATIONS.md` §2.2, `docs/RUNBOOKS.md` «Maintenance window») | **OPEN — operator only** |

Local evidence for steps 3–6, simulated ENOSPC/pool pressure and disposable
backup/restore rehearsals exists in `docs/TESTING.md` §2 and
`docs/PERFORMANCE.md`; none of it discharges a row above.
