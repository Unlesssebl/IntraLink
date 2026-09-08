# ADR 0001: PostgreSQL as the command system of record

Status: accepted — 2026-09-05

## Context

The original command path committed a partial `job_log` record and then published directly to Redis. A database failure could be ignored, Redis delivery was acknowledged even when result persistence failed, and confirmation occupied a Windows worker slot. Redis also held the most complete command state, so restarts could lose the audit trail.

## Decision

PostgreSQL owns command state, approvals, attempts, events, policies, Inbox and Outbox records. Redis Streams transport messages only. Creation and Outbox insertion happen in one transaction. A relay publishes Outbox rows at least once; workers atomically claim a command with a lease and persist the result before `XACK`.

Only `diagnose_host` and `rag_sync` may run automatically. State-changing actions wait in `awaiting_approval`. An expired lease for a state-changing action becomes `needs_review`; safe actions retry after 5 and 30 seconds and stop after the third attempt. Unsupported actions are rejected before queueing.

Browser operator decisions require an admin JWT. Telegram decisions use the bot service key and are accepted only for an operator registered in the `users` table; the resolved corporate login and Telegram ID are written to the approval audit. Windows workers use a separate `WORKER_API_KEY`. The public v2 API derives the initiator from authentication and requires an `Idempotency-Key` for every create request.

## Operational model

- `GET /health` is process liveness.
- `GET /ready` verifies Redis and the exact Alembic schema revision.
- `GET /metrics` exposes durable command state and pending Outbox depth.
- `GET /api/v2/commands/{id}/events` replays PostgreSQL events using SSE and `Last-Event-ID`.
- `command-worker` relays the Outbox, reconciles leases and executes backend commands.
- Windows execution uses `stream:execution_commands:v2`; backend execution uses `stream:backend_commands:v2`.

## Rollout

1. Set `APP_ENV=production`, `JWT_SECRET`, `BOT_API_KEY`, `WORKER_API_KEY`, `CORS_ORIGINS` and service credentials.
2. Create a PostgreSQL backup with `scripts/backup-postgres.ps1` and perform an actual scratch restore with `scripts/verify-postgres-restore.ps1 -DumpPath <dump>`.
3. Start `db-migrate`; application services wait for its successful exit.
4. Start Core API and `command-worker`; require `/ready` to return 200.
5. Upgrade the Windows worker and clients to v2.
6. Observe `intralink_command_outbox_pending` and commands in `needs_review`.
7. Remove the v1 command router after the compatibility window.

The initial migration is a clean baseline. Existing ad hoc schemas must be exported and re-imported through an explicit data migration; startup will not alter them implicitly.

## Consequences

Redis loss no longer loses authoritative command state. Duplicate delivery is expected and harmless. Operators must resolve uncertain state-changing outcomes explicitly. PostgreSQL availability is required for command execution and readiness.
