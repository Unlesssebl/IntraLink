# Decisioning modernization checkpoint

Date: 2026-09-07

## Completed in this checkpoint

- Added strict versioned domain contracts and deterministic person validation in `shared/domain`.
- Fixed regression #140479: invalid placeholder identity data produces `account_details_invalid`, status 35, and no `create_user` command.
- Added explicit ordered rule registry and a compatibility adapter for typed outcomes.
- Added versioned PostgreSQL `response_templates` and `resolution_policies`, strict Jinja sandbox rendering, v2 admin APIs, legacy API compatibility, and removal of unused `triage_rules`.
- Added migration `20260907_0007`; upgrade, downgrade, and repeated upgrade were verified on the local PostgreSQL database without duplicate active records.
- Added `CreateUserHandler` with typed input, duplicated validation, DC/OU/group and collision preflight, execution, read-after-write verification, reconciliation, and `NEVER_AUTO_RETRY`.
- Added encrypted TTL command secret artifacts. Temporary passwords are excluded from command results, Redis payloads, decision records, and logs.
- Added schema-constrained Ollama extraction fallback behind `off | shadow | enabled`; it cannot override explicit invalid structured identity fields.
- Implemented Core API delivery/finalization boundary for verified `create_user` commands (`CommandDeliveryService` + `POST /api/v2/commands/{id}/deliver`):
  1. Resolves `user_created` policy/template strictly after command `succeeded` with verified evidence.
  2. Decrypts `temporary_password` inside Core API, renders once with `StrictUndefined` Jinja sandbox, transitions ticket to status 27 and then status 29 with expenses in IntraService.
  3. Uses two-phase secret lifecycle (`peek` -> publish -> `wipe`): retains encrypted artifact on retryable IntraService failure and wipes it strictly after successful publication.
  4. Returns only delivery metadata, never plaintext credentials.
  5. Added integration tests covering successful delivery, verification mismatch, expired/already-consumed artifacts, IntraService failure retry retention, and API endpoints.
- Added regression, contract, property-based, resolver, extraction, secret, delivery, and Worker handler tests (16/16 passed).

## Verification status

- Targeted decisioning suite: 16 passed.
- Command claims & active execution tests: passed.
- Ruff check passed for all touched Python modules.
- Local PostgreSQL revision: `20260907_0007`.

## Next continuation point

Phases 6 & 7 of the modernization plan (`plan.md`):

1. **Phase 6 (LLM extraction):** offline evaluation / canary comparison on sample tickets, confirming precision and abstention before enabling as runtime fallback.
2. **Phase 7 (Typed migration of remaining rules):** incrementally migrate `PrinterRule`, `OfflineHostRule`, `ServiceRedirectRule`, `PhysicalDeviceRule`, `FileLockRule`, and RAG matching to typed domain outcomes (`DecisionOutcome`).
3. **Phase 8 (Cleanup):** deprecation cutover after release window.
