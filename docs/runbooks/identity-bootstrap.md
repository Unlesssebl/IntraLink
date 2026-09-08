# Identity bootstrap and service credentials

Apply Alembic migrations before provisioning identities:

```powershell
docker compose run --rm db-migrate
```

Create the first PostgreSQL-backed administrator once:

```powershell
docker compose run --rm core-api python -m app.identity_cli bootstrap-admin --username user.name
```

The user then signs in through the operator panel with corporate credentials. `ADMIN_LOGINS`
is read only by the non-production compatibility mode and must not be used to grant production
access.

An administrator provisions one credential per component through
`POST /api/v2/identities/service-credentials`. The response shows the secret once. Recommended
scopes are:

| Component | Scopes |
|---|---|
| Telegram bot | `task:read`, `task:mutate`, `command:read`, `command:create`, `telegram:link`, `telegram:challenge:issue`, `telegram:challenge:consume` |
| Helpdesk CLI | `task:read`, `triage:read`, `triage:mutate`, `ai:use`, `command:read`, `command:create` |
| IntraLink MCP | only the scopes needed by enabled MCP tools |
| Windows worker | `command:claim:windows`, `command:finish:windows` |
| Backend worker | `command:claim:backend`, `command:finish:backend` |

Set the resulting pairs in the component-specific environment variables shown in
`.env.example`. During rotation, create the replacement first, deploy it, confirm `last_used_at`,
and then revoke the old `key_id` with `DELETE /api/v2/identities/service-credentials/{key_id}`.

Production must set `APP_ENV=production` and `ALLOW_LEGACY_SHARED_KEYS=false`. Shared
`BOT_API_KEY`, `WORKER_API_KEY`, query-string JWTs, Telegram password login, and the legacy
Command API v1 are rejected in that mode.
