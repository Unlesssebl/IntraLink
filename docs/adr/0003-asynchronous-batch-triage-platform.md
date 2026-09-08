# ADR 0003: Asynchronous Batch Triage via Command Worker and Durable Progress

Status: proposed — 2026-09-08

## Context

Prior to this decision, triage analysis of Helpdesk tickets in IntraService was triggered via a synchronous HTTP endpoint `POST /api/v1/triage/analyze-batch`. When an operator initiated analysis of an entire queue filter (up to 200 tickets), the client maintained an open HTTP connection for 60–120+ seconds while an in-memory `asyncio.gather` ran against `TRIAGE_ANALYSIS_MAX_CONCURRENCY=4`.

This approach introduced several critical liabilities:
1. **Network Fragility:** Corporate proxies, API gateways, and cloud balancers with 60-second read timeouts abort long-running requests with `504 Gateway Timeout`.
2. **In-Flight Volatility:** Server restarts (deploys, scaling, health-check recycles) killed in-memory batch coroutines, leaving active Redis locks stranded and queues in indeterminate state.
3. **Architectural Deviation:** While mutations already use the transactional `Command Platform v2` (ADR 0001) with durable leases, Outbox, and workers, batch triage bypassed this architecture and placed heavy compute directly on the FastAPI web event loop.
4. **UX Blackout:** Operators faced a 2-minute frozen UI without granular progress, cancellation capability, or resilience against transient SSE drops.

## Decision

Batch ticket analysis is decoupled from the synchronous HTTP request lifecycle and converted into an **Asynchronous Durable Batch Job**:

1. **Fast Ingress (202 Accepted):**
   `POST /api/v1/triage/analyze-batch` validates the requested ticket IDs, enforces deduplication, registers a `BatchRecord` in PostgreSQL/Redis, and immediately returns HTTP `202 Accepted` with a deterministic `batch_id` in under 30 milliseconds.

2. **Durable Worker Execution:**
   Execution is handled by the existing backend command pipeline (`command_worker`) using bounded worker pools.
   - Individual ticket analyses reuse the existing fencing token and lease mechanism (`acquire_analysis_lease`).
   - The worker periodically updates durable progress in Redis (`batch:triage:{batch_id}`) with an active TTL heartbeat.

3. **Multi-Channel Real-time Progress:**
   - As each ticket analysis is finalized, an event `task_analyzed` is published to the Redis Pub/Sub stream (`events:all`) and relayed via existing SSE (`/api/v1/events/stream`).
   - The payload contains `{ batch_id, task_id, status, analysis, progress: { processed, total } }`.
   - On batch completion or abortion, a final `batch_completed` event is emitted.

4. **Fault Tolerance & Edge Case Safeguards:**
   - **Reconciliation on Reconnect:** Clients querying `GET /api/v1/triage/analyze-batch/{batch_id}` reconcile state if SSE temporarily drops.
   - **Idempotent Single-Flight:** If an active queue batch is already executing, subsequent requests attach to the running `batch_id` rather than duplicating the computational workload.
   - **Cooperative Cancellation:** An endpoint `POST /api/v1/triage/analyze-batch/{batch_id}/cancel` sets an abort flag in Redis, enabling operators to cleanly halt runaway batches.
   - **Frontend Event Throttling:** The React UI buffers incoming SSE analysis events and flushes state in 350ms windows, preventing DOM thrashing and micro-stutters during high-frequency updates.

## Operational Model

- `POST /api/v1/triage/analyze-batch` -> 202 Accepted (`{"batch_id": "...", "total": N, "status": "queued"}`).
- `GET /api/v1/triage/analyze-batch/{batch_id}` -> Returns live status, completed count, failed count, and ticket results.
- `POST /api/v1/triage/analyze-batch/{batch_id}/cancel` -> Signals cooperative worker cancellation.
- SSE stream `/api/v1/events/stream?channel=all` relays `task_analyzed` and `batch_completed`.

## Consequences

- Web ingress is protected from long-running HTTP timeouts.
- Browser tab closure or reload no longer disrupts queue analysis.
- Resource usage on IntraService, RAG, and LLM backends is strictly bounded by backend concurrency controls.
- Operators gain real-time visibility into queue triage progression with cancellation controls.
