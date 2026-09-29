"""Directory provider protocol defining abstract Active Directory contracts."""

from typing import Optional, Protocol, Sequence, Tuple, runtime_checkable


@runtime_checkable
class DirectoryProviderProtocol(Protocol):
    """Protocol declaring contracts for directory service backends (AD/LDAP)."""

    def probe_credentials_sync(
        self,
        *,
        servers: Sequence[str],
        username: str,
        password: str,
        port: int = 636,
        use_ssl: bool = True,
        root_ou: Optional[str] = None,
        connect_timeout: float = 2.0,
        domain: str = "corporate.loc",
    ) -> Tuple[str, int]:
        """Synchronously verify credentials against domain controllers and return (active_server, ou_count)."""
        ...

    async def probe_credentials(
        self,
        *,
        servers: Sequence[str],
        username: str,
        password: str,
        port: int = 636,
        use_ssl: bool = True,
        root_ou: Optional[str] = None,
        connect_timeout: float = 2.0,
        domain: str = "corporate.loc",
    ) -> Tuple[str, int]:
        """Asynchronously verify credentials against domain controllers and return (active_server, ou_count)."""
        ...
