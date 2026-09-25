"""Active Directory LDAP Connection Pool with Round Robin and strict timeout limits."""

import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Generator, List, Optional, Tuple

import ldap3
from ldap3 import ALL, Connection, ROUND_ROBIN, Server, ServerPool
from ldap3.core.exceptions import LDAPException
from pydantic import BaseModel, Field

logger = logging.getLogger("intralink.core.ad.pool")


class ADPoolConfig(BaseModel):
    """Configuration for Active Directory LDAP connection pool."""

    servers: List[str] = Field(default_factory=list)
    connect_timeout: float = 2.0  # Max 2.0s connection timeout invariant
    port: int = 389
    use_ssl: bool = False
    bind_user: Optional[str] = None
    bind_password: Optional[str] = None
    domain: str = "corporate.loc"

    @classmethod
    def from_env(cls) -> "ADPoolConfig":
        raw_servers = os.getenv("AD_SERVERS") or os.getenv("AD_DOMAIN_CONTROLLERS") or ""
        servers = [s.strip() for s in raw_servers.split(",") if s.strip()]
        if not servers:
            # Fallback to default domain controllers
            servers = ["dc1.corporate.loc", "dc2.corporate.loc"]

        timeout_str = os.getenv("AD_CONNECT_TIMEOUT", "2.0")
        try:
            connect_timeout = float(timeout_str)
        except ValueError:
            connect_timeout = 2.0

        port = int(os.getenv("AD_PORT", "389"))
        use_ssl = os.getenv("AD_USE_SSL", "false").lower() in ("true", "1", "yes")
        bind_user = os.getenv("AD_BIND_USER") or os.getenv("AD_USER")
        bind_password = os.getenv("AD_BIND_PASSWORD") or os.getenv("AD_PASSWORD")
        domain = os.getenv("AD_DOMAIN", "corporate.loc")

        return cls(
            servers=servers,
            connect_timeout=connect_timeout,
            port=port,
            use_ssl=use_ssl,
            bind_user=bind_user,
            bind_password=bind_password,
            domain=domain,
        )


class ActiveDirectoryPool:
    """Thread-safe LDAP ServerPool managing Active Directory domain controller connections."""

    def __init__(self, config: Optional[ADPoolConfig] = None) -> None:
        self.config = config or ADPoolConfig.from_env()
        self._servers: List[Server] = [
            Server(
                host=srv,
                port=self.config.port,
                use_ssl=self.config.use_ssl,
                connect_timeout=self.config.connect_timeout,
                get_info=ALL,
            )
            for srv in self.config.servers
        ]
        self._pool = ServerPool(
            self._servers,
            pool_strategy=ROUND_ROBIN,
            active=True,
            exhaust=True,
        )
        logger.info(
            "Initialized ActiveDirectoryPool with %d servers: %s (timeout=%.1fs)",
            len(self.config.servers),
            ", ".join(self.config.servers),
            self.config.connect_timeout,
        )

    @property
    def server_pool(self) -> ServerPool:
        return self._pool

    def get_connection(
        self,
        user: Optional[str] = None,
        password: Optional[str] = None,
        auto_bind: bool = True,
        read_only: bool = False,
    ) -> Connection:
        """Create a new LDAP connection bound to the server pool."""
        bind_user = user or self.config.bind_user
        bind_password = password or self.config.bind_password

        # Normalize username with domain if needed
        if bind_user and "@" not in bind_user and "\\" not in bind_user:
            bind_user = f"{bind_user}@{self.config.domain}"

        conn = Connection(
            self._pool,
            user=bind_user,
            password=bind_password,
            auto_bind=auto_bind,
            read_only=read_only,
            raise_exceptions=True,
        )
        return conn

    @contextmanager
    def connection_scope(
        self,
        user: Optional[str] = None,
        password: Optional[str] = None,
        auto_bind: bool = True,
        read_only: bool = False,
    ) -> Generator[Connection, None, None]:
        """Context manager for safely acquiring and releasing an LDAP connection."""
        conn = self.get_connection(
            user=user,
            password=password,
            auto_bind=auto_bind,
            read_only=read_only,
        )
        try:
            yield conn
        finally:
            if conn.bound:
                try:
                    conn.unbind()
                except Exception:
                    pass

    def check_liveness_sync(self) -> Tuple[bool, str, Optional[str]]:
        """Synchronously probe domain controllers in pool with 2.0s connection budget."""
        try:
            with self.connection_scope(auto_bind=True, read_only=True) as conn:
                active_host = conn.server.host if conn.server else "unknown"
                return True, "Active Directory DC is responsive", active_host
        except LDAPException as exc:
            return False, f"LDAP error: {exc}", None
        except Exception as exc:
            return False, f"Connection failed: {exc}", None

    async def check_liveness(self) -> Tuple[bool, str, Optional[str]]:
        """Non-blocking asynchronous health check for the AD pool."""
        return await asyncio.to_thread(self.check_liveness_sync)
