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

    def get_computer_sync(self, computer_name: str) -> Optional[dict]:
        """Search for a computer account in Active Directory by hostname."""
        if not computer_name or not computer_name.strip():
            return None
        cleaned = computer_name.strip().rstrip("$")
        search_filter = f"(|(sAMAccountName={cleaned}$)(cn={cleaned}))"
        search_base = f"DC={self.config.domain.replace('.', ',DC=')}"
        try:
            with self.connection_scope(auto_bind=True, read_only=True) as conn:
                conn.search(
                    search_base=search_base,
                    search_filter=search_filter,
                    attributes=["cn", "sAMAccountName", "dNSHostName", "operatingSystem", "distinguishedName", "lastLogonTimestamp"],
                    size_limit=1,
                )
                if conn.entries:
                    entry = conn.entries[0]
                    return {
                        "cn": str(entry.cn.value) if hasattr(entry, "cn") else cleaned,
                        "sam_account_name": str(entry.sAMAccountName.value) if hasattr(entry, "sAMAccountName") else None,
                        "dns_hostname": str(entry.dNSHostName.value) if hasattr(entry, "dNSHostName") else None,
                        "operating_system": str(entry.operatingSystem.value) if hasattr(entry, "operatingSystem") else None,
                        "distinguished_name": str(entry.distinguishedName.value) if hasattr(entry, "distinguishedName") else None,
                    }
                return None
        except Exception as exc:
            logger.debug("ActiveDirectoryPool.get_computer failed for %s: %s", computer_name, exc)
            return None

    async def get_computer(self, computer_name: str) -> Optional[dict]:
        """Asynchronously search for a computer account in Active Directory."""
        return await asyncio.to_thread(self.get_computer_sync, computer_name)


_default_ad_pool: Optional[ActiveDirectoryPool] = None


def get_default_ad_pool() -> ActiveDirectoryPool:
    global _default_ad_pool
    if _default_ad_pool is None:
        _default_ad_pool = ActiveDirectoryPool()
    return _default_ad_pool


def set_default_ad_pool(pool: Optional[ActiveDirectoryPool]) -> None:
    global _default_ad_pool
    _default_ad_pool = pool


async def is_valid_domain_computer(
    hostname: str,
    redis_client: Optional[object] = None,
    ad_pool: Optional[ActiveDirectoryPool] = None,
    domain: Optional[str] = None,
) -> bool:
    """Verify whether a hostname corresponds to a real corporate domain workstation/server.

    1. Checks Redis cache `cache:ad:computer:{hostname}` (TTL 24h).
    2. Fast DNS lookup (< 400ms) with domain suffix.
    3. ActiveDirectoryPool.get_computer() lookup (< 2.0s).
    4. Caches both positive and negative results in Redis to guarantee < 2ms latency on repeat calls.
    """
    if not hostname or not str(hostname).strip():
        return False

    clean_host = str(hostname).strip().upper()
    cache_key = f"cache:ad:computer:{clean_host}"

    # 1. Check Redis cache
    if redis_client is not None:
        try:
            cached_val = await redis_client.get(cache_key)  # type: ignore[attr-defined]
            if cached_val is not None:
                if isinstance(cached_val, bytes):
                    cached_val = cached_val.decode("utf-8")
                return cached_val == "1"
        except Exception as exc:
            logger.debug("Redis lookup error in is_valid_domain_computer for %s: %s", clean_host, exc)

    # 2. Fast DNS lookup
    try:
        from core.diagnostic.ping import resolve_dns_fast

        ip = await resolve_dns_fast(clean_host)
        if ip:
            if redis_client is not None:
                try:
                    await redis_client.set(cache_key, "1", ex=86400)  # type: ignore[attr-defined]
                except Exception:
                    pass
            return True
    except Exception as exc:
        logger.debug("DNS check error in is_valid_domain_computer for %s: %s", clean_host, exc)

    # 3. Active Directory LDAP search
    pool = ad_pool
    if pool is None:
        try:
            pool = get_default_ad_pool()
        except Exception:
            pool = None

    if pool is not None:
        try:
            computer_entry = await pool.get_computer(clean_host)
            if computer_entry:
                if redis_client is not None:
                    try:
                        await redis_client.set(cache_key, "1", ex=86400)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                return True
        except Exception as exc:
            logger.debug("AD lookup error in is_valid_domain_computer for %s: %s", clean_host, exc)

    # 4. Neither DNS nor AD validated the host -> negative cache (TTL 24h)
    if redis_client is not None:
        try:
            await redis_client.set(cache_key, "0", ex=86400)  # type: ignore[attr-defined]
        except Exception:
            pass

    return False

