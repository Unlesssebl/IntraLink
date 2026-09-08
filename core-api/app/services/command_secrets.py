import datetime as dt
import hashlib
import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import CommandRecord, CommandSecretArtifact
from app.services.crypto import decrypt_secret, encrypt_secret


class CommandSecretService:
    """One-time encrypted artifacts; plaintext is never returned by command APIs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def store(
        self,
        command_id: uuid.UUID,
        *,
        claim_token: str,
        name: str,
        value: str,
        ttl_seconds: int,
    ) -> CommandSecretArtifact:
        command = await self.db.scalar(
            select(CommandRecord)
            .where(CommandRecord.id == command_id)
            .with_for_update()
        )
        if command is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Command not found")
        token_hash = hashlib.sha256(claim_token.encode()).hexdigest()
        if command.status != "running" or not secrets.compare_digest(
            command.lease_token_hash or "", token_hash
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "Stale or invalid claim")
        if name != "temporary_password":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported secret artifact"
            )
        existing = await self.db.scalar(
            select(CommandSecretArtifact).where(
                CommandSecretArtifact.command_id == command_id,
                CommandSecretArtifact.name == name,
            )
        )
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Secret artifact already exists"
            )
        artifact = CommandSecretArtifact(
            command_id=command_id,
            name=name,
            encrypted_value=encrypt_secret(value),
            content_type="text/plain",
            expires_at=dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(seconds=ttl_seconds),
        )
        self.db.add(artifact)
        await self.db.commit()
        await self.db.refresh(artifact)
        return artifact

    async def peek(
        self, command_id: uuid.UUID, *, name: str
    ) -> tuple[CommandSecretArtifact, str]:
        artifact = await self.db.scalar(
            select(CommandSecretArtifact)
            .where(
                CommandSecretArtifact.command_id == command_id,
                CommandSecretArtifact.name == name,
            )
            .with_for_update()
        )
        if artifact is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Secret artifact not found")
        now = dt.datetime.now(dt.timezone.utc)
        expires_at = artifact.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
        if artifact.consumed_at is not None or expires_at <= now:
            raise HTTPException(
                status.HTTP_410_GONE, "Secret artifact expired or consumed"
            )
        value = decrypt_secret(artifact.encrypted_value)
        return artifact, value

    async def wipe(self, artifact: CommandSecretArtifact) -> CommandSecretArtifact:
        artifact.consumed_at = dt.datetime.now(dt.timezone.utc)
        artifact.encrypted_value = ""
        await self.db.commit()
        await self.db.refresh(artifact)
        return artifact

    async def consume(
        self, command_id: uuid.UUID, *, name: str
    ) -> tuple[CommandSecretArtifact, str]:
        artifact, value = await self.peek(command_id, name=name)
        await self.wipe(artifact)
        return artifact, value

    async def cleanup_expired_secrets(self) -> int:
        """Grave digger: delete expired or already consumed secret artifacts."""
        from sqlalchemy import delete

        now = dt.datetime.now(dt.timezone.utc)
        stmt = delete(CommandSecretArtifact).where(
            (CommandSecretArtifact.expires_at < now)
            | (CommandSecretArtifact.consumed_at.is_not(None))
        )
        res = await self.db.execute(stmt)
        await self.db.commit()
        return res.rowcount or 0

