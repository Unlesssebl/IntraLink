"""One-time identity bootstrap commands for deployment operators."""

import argparse
import asyncio

from sqlalchemy import select

from app.database.db import AsyncSessionLocal, Principal, PrincipalRole
from app.services.identity import ensure_rbac_catalog, record_security_event


async def bootstrap_admin(username: str) -> None:
    subject = username.strip().lower()
    if not subject:
        raise SystemExit("username must not be empty")
    async with AsyncSessionLocal() as db:
        await ensure_rbac_catalog(db, commit=False)
        principal = await db.scalar(
            select(Principal).where(Principal.type == "human", Principal.subject == subject)
        )
        if principal is None:
            principal = Principal(
                type="human", subject=subject, display_name=username.strip(), status="active"
            )
            db.add(principal)
            await db.flush()
        elif principal.status != "active":
            principal.status = "active"
        if await db.get(PrincipalRole, (principal.id, "system_admin")) is None:
            db.add(PrincipalRole(principal_id=principal.id, role_name="system_admin"))
        await record_security_event(
            db,
            event_type="identity.bootstrap_admin",
            outcome="success",
            principal_id=principal.id,
            resource_type="principal",
            resource_id=str(principal.id),
            details={"username": subject},
        )
        await db.commit()
    print(f"System administrator ready: {subject}")


def main() -> None:
    parser = argparse.ArgumentParser(description="IntraLink identity bootstrap")
    subparsers = parser.add_subparsers(dest="command", required=True)
    admin = subparsers.add_parser("bootstrap-admin")
    admin.add_argument("--username", required=True)
    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        asyncio.run(bootstrap_admin(args.username))


if __name__ == "__main__":
    main()
