import asyncio
import logging
import ssl
import time
from typing import Any
from pydantic import BaseModel, Field

import ldap3
from ldap3.core.exceptions import LDAPException

logger = logging.getLogger(__name__)


class LDAPSConfig(BaseModel):
    server: str = Field(..., description="Хост или IP-адрес контроллера домена")
    port: int = Field(636, description="Порт LDAPS (по умолчанию 636)")
    use_ssl: bool = Field(True, description="Использовать SSL/TLS (LDAPS)")
    user_dn: str = Field(
        ...,
        description="Учетная запись для подключения (UPN, например: svc_intralink@corp.loc)",
    )
    password: str = Field(..., description="Пароль сервисной учетной записи")
    base_dn: str = Field(
        ..., description="Базовый DN каталога (например: DC=corporate,DC=loc)"
    )
    wlan_group_name: str = Field(
        "WLAN-WORKNET", description="Имя целевой группы AD для Wi-Fi доступа"
    )
    domain_name: str = Field(
        "corporate.loc", description="Имя домена (например: corporate.loc)"
    )


class ConnectionTestResult(BaseModel):
    success: bool
    latency_ms: float
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


def _sync_test_connection(config: LDAPSConfig) -> ConnectionTestResult:
    """
    Синхронная проверка связи с контроллером домена по LDAPS.
    Использует системное хранилище доверенных сертификатов (/etc/ssl/certs).
    """
    start_time = time.perf_counter()
    try:
        tls_config = None
        if config.use_ssl:
            ssl_context = ssl.create_default_context()
            tls_config = ldap3.Tls(
                validate=ssl.CERT_REQUIRED,
                version=ssl.PROTOCOL_TLS_CLIENT,
            )

        server = ldap3.Server(
            host=config.server,
            port=config.port,
            use_ssl=config.use_ssl,
            tls=tls_config,
            connect_timeout=10,
        )

        conn = ldap3.Connection(
            server=server,
            user=config.user_dn,
            password=config.password,
            authentication=ldap3.SIMPLE,
            auto_bind=True,
            check_names=True,
            raise_exceptions=True,
        )

        latency = (time.perf_counter() - start_time) * 1000

        # Выполняем базовый поисковый запрос для проверки прав чтения
        conn.search(
            search_base=config.base_dn,
            search_filter="(objectClass=*)",
            search_scope=ldap3.BASE,
            attributes=["defaultNamingContext", "subschemaSubentry"],
        )

        search_success = bool(conn.entries)

        conn.unbind()

        return ConnectionTestResult(
            success=True,
            latency_ms=round(latency, 2),
            message="Успешное подключение и аутентификация в Active Directory по LDAPS",
            details={
                "server": config.server,
                "port": config.port,
                "ssl": config.use_ssl,
                "user": config.user_dn,
                "base_dn": config.base_dn,
                "search_verified": search_success,
            },
        )
    except LDAPException as e:
        latency = (time.perf_counter() - start_time) * 1000
        logger.warning("LDAPS connection failed: %s", e)
        return ConnectionTestResult(
            success=False,
            latency_ms=round(latency, 2),
            message=f"Ошибка LDAP: {str(e)}",
            details={"error_type": type(e).__name__},
        )
    except Exception as e:
        latency = (time.perf_counter() - start_time) * 1000
        logger.exception("Unexpected error testing LDAPS: %s", e)
        return ConnectionTestResult(
            success=False,
            latency_ms=round(latency, 2),
            message=f"Сетевая или системная ошибка: {str(e)}",
            details={"error_type": type(e).__name__},
        )


async def test_ldaps_connection(config: LDAPSConfig) -> ConnectionTestResult:
    """Асинхронная обертка для проверки связи с LDAPS."""
    return await asyncio.to_thread(_sync_test_connection, config)


def _sync_add_computer_to_wlan_group(
    computer_name: str, config: LDAPSConfig
) -> dict[str, Any]:
    """
    Синхронное добавление учетной записи компьютера в группу WLAN-WORKNET через LDAPS.
    """
    clean_pc = computer_name.strip().upper()
    if clean_pc.endswith("$"):
        clean_pc = clean_pc[:-1]

    tls_config = None
    if config.use_ssl:
        tls_config = ldap3.Tls(
            validate=ssl.CERT_REQUIRED,
            version=ssl.PROTOCOL_TLS_CLIENT,
        )

    server = ldap3.Server(
        host=config.server,
        port=config.port,
        use_ssl=config.use_ssl,
        tls=tls_config,
        connect_timeout=10,
    )

    with ldap3.Connection(
        server=server,
        user=config.user_dn,
        password=config.password,
        authentication=ldap3.SIMPLE,
        auto_bind=True,
    ) as conn:
        # 1. Поиск компьютера в AD
        comp_filter = f"(&(objectCategory=computer)(|(sAMAccountName={clean_pc}$)(cn={clean_pc})))"
        conn.search(
            search_base=config.base_dn,
            search_filter=comp_filter,
            search_scope=ldap3.SUBTREE,
            attributes=["distinguishedName", "sAMAccountName"],
        )

        if not conn.entries:
            raise ValueError(f"Компьютер '{clean_pc}' не найден в каталоге Active Directory ({config.base_dn})")

        computer_dn = conn.entries[0].distinguishedName.value

        # 2. Поиск целевой группы WLAN
        group_name = config.wlan_group_name.strip()
        group_filter = f"(&(objectCategory=group)(|(sAMAccountName={group_name})(cn={group_name})))"
        conn.search(
            search_base=config.base_dn,
            search_filter=group_filter,
            search_scope=ldap3.SUBTREE,
            attributes=["distinguishedName", "member"],
        )

        if not conn.entries:
            raise ValueError(f"Группа '{group_name}' не найдена в каталоге Active Directory ({config.base_dn})")

        group_entry = conn.entries[0]
        group_dn = group_entry.distinguishedName.value

        # Проверяем, состоит ли ПК уже в группе
        current_members = group_entry.member.values if hasattr(group_entry, "member") and group_entry.member else []
        if any(computer_dn.lower() == str(m).lower() for m in current_members):
            return {
                "success": True,
                "already_member": True,
                "computer_name": clean_pc,
                "computer_dn": computer_dn,
                "group_name": group_name,
                "group_dn": group_dn,
                "message": f"Компьютер {clean_pc} уже является членом группы {group_name}",
            }

        # 3. Добавление ПК в группу
        success = conn.modify(
            group_dn,
            {"member": [(ldap3.MODIFY_ADD, [computer_dn])]},
        )

        if not success:
            res = conn.result
            if res.get("result") == 68:  # entryAlreadyExists
                return {
                    "success": True,
                    "already_member": True,
                    "computer_name": clean_pc,
                    "computer_dn": computer_dn,
                    "group_name": group_name,
                    "group_dn": group_dn,
                    "message": f"Компьютер {clean_pc} уже состоит в группе {group_name}",
                }
            raise RuntimeError(
                f"Ошибка модификации группы AD ({res.get('description', 'Unknown')}): {res.get('message', '')}"
            )

        return {
            "success": True,
            "already_member": False,
            "computer_name": clean_pc,
            "computer_dn": computer_dn,
            "group_name": group_name,
            "group_dn": group_dn,
            "message": f"Компьютер {clean_pc} успешно добавлен в группу {group_name}",
        }


async def add_computer_to_wlan_group(
    computer_name: str, config: LDAPSConfig
) -> dict[str, Any]:
    """Асинхронная обертка для добавления ПК в группу Wi-Fi."""
    return await asyncio.to_thread(_sync_add_computer_to_wlan_group, computer_name, config)
