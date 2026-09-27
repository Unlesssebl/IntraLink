"""Read-only Preflight verification service for Evidence-Based Routing Cascade.

Performs fast, side-effect-free environmental and identity validation prior to command creation.
Stores append-only preflight records with a strict 120-second TTL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import ldap3
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.ad.pool import ActiveDirectoryPool
from core.ad.transliteration import generate_sam_account_name
from core.autopilot.dto import PreflightCheckDTO, PreflightResultDTO
from core.database.models import sanitize_secrets
from core.diagnostic.ports import FastSocketProbe
from core.routing.contracts import TicketSnapshot
from core.routing.readiness import get_fact

logger = logging.getLogger("core.routing.preflight")

PREFLIGHT_TTL_SECONDS = 120
EXECUTABLE_PREFLIGHT_STATUSES = frozenset({"passed", "not_applicable"})


def is_executable_preflight_status(preflight_status: str) -> bool:
    """Return whether a preflight result is allowed to back an executable plan."""
    return preflight_status in EXECUTABLE_PREFLIGHT_STATUSES


def compute_canonical_params_hash(params: Dict[str, Any]) -> str:
    """Deterministic canonical SHA-256 hash of parameter dictionary."""
    sanitized = sanitize_secrets(params) if params else {}
    canonical_json = json.dumps(
        sanitized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_canonical_plan_hash(
    task_id: int,
    decision_id: Optional[uuid.UUID],
    snapshot_hash: str,
    scenario_key: str,
    proposed_params: Dict[str, Any],
    suggested_comment: str = "",
    target_status_id: int = 3,
) -> str:
    """Deterministic canonical SHA-256 hash representing the full prepared operator plan."""
    payload = {
        "task_id": task_id,
        "decision_id": str(decision_id) if decision_id else None,
        "snapshot_hash": snapshot_hash,
        "scenario_key": scenario_key,
        "proposed_params": sanitize_secrets(proposed_params) if proposed_params else {},
        "suggested_comment": suggested_comment.strip(),
        "target_status_id": target_status_id,
    }
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_canonical_plan_hash_from_json(plan_json: Dict[str, Any]) -> str:
    """Recompute the canonical hash using only the persisted plan payload."""
    required_fields = (
        "task_id",
        "decision_id",
        "snapshot_hash",
        "scenario_key",
        "proposed_params",
        "suggested_comment",
        "target_status_id",
    )
    missing_fields = [field for field in required_fields if field not in plan_json]
    if missing_fields:
        raise ValueError(f"plan_json is missing canonical fields: {', '.join(missing_fields)}")

    decision_id = plan_json["decision_id"]
    if decision_id is not None and not isinstance(decision_id, uuid.UUID):
        decision_id = uuid.UUID(str(decision_id))

    return compute_canonical_plan_hash(
        task_id=int(plan_json["task_id"]),
        decision_id=decision_id,
        snapshot_hash=str(plan_json["snapshot_hash"]),
        scenario_key=str(plan_json["scenario_key"]),
        proposed_params=dict(plan_json["proposed_params"] or {}),
        suggested_comment=str(plan_json["suggested_comment"] or ""),
        target_status_id=int(plan_json["target_status_id"]),
    )


class RoutingPreflightService:
    """Service executing read-only environmental probes and identity checks without executing scenarios."""

    def __init__(
        self,
        ad_pool: Optional[ActiveDirectoryPool] = None,
    ) -> None:
        self._ad_pool = ad_pool

    def _get_ad_pool(self) -> ActiveDirectoryPool:
        if self._ad_pool is None:
            self._ad_pool = ActiveDirectoryPool()
        return self._ad_pool

    async def execute_preflight(
        self,
        decision_id: uuid.UUID,
        snapshot: TicketSnapshot,
        scenario_key: str,
        params: Dict[str, Any],
        session: Optional[AsyncSession] = None,
    ) -> PreflightResultDTO:
        """Execute scenario-specific read-only preflight checks and persist immutable record."""
        params_hash = compute_canonical_params_hash(params)
        checks: List[PreflightCheckDTO] = []
        details: Dict[str, Any] = {}
        status = "passed"
        error_msg: Optional[str] = None

        try:
            if scenario_key in ("install_printer", "printer_spooler_restart", "default_printer_fix"):
                status, checks, details = await self._preflight_printer(snapshot, params, scenario_key)

            elif scenario_key == "offline_host":
                status, checks, details = await self._preflight_offline_host(snapshot, params)

            elif scenario_key in ("grant_wlan", "account_lock"):
                status, checks, details = await self._preflight_ad_user(snapshot, params, scenario_key)

            elif scenario_key == "account_create":
                status, checks, details = await self._preflight_account_create(snapshot, params)

            elif scenario_key == "rag_consultation":
                status, checks, details = await self._preflight_rag(snapshot, session)

            elif scenario_key == "service_redirect":
                status = "not_applicable"
                checks.append(PreflightCheckDTO(name="service_redirect", status="passed", message="No preflight required"))

            else:
                status = "not_applicable"
                checks.append(PreflightCheckDTO(name="generic_scenario", status="passed", message="No preflight required"))

        except TimeoutError as exc:
            logger.warning("Preflight timed out for task #%d (%s): %s", snapshot.task_id, scenario_key, exc)
            status = "degraded"
            error_msg = "Превышен таймаут выполнения preflight-проверки"
            checks.append(PreflightCheckDTO(name="timeout_guard", status="failed", message=error_msg))
        except Exception as exc:
            logger.warning("Preflight check failure for task #%d (%s): %s", snapshot.task_id, scenario_key, exc)
            status = "degraded"
            error_msg = "Ошибка при выполнении preflight-проверки"
            checks.append(PreflightCheckDTO(name="execution_guard", status="failed", message=error_msg))

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=PREFLIGHT_TTL_SECONDS)

        preflight_id = uuid.uuid4()
        result_dto = PreflightResultDTO(
            id=preflight_id,
            status=status,
            scenario_key=scenario_key,
            params_hash=params_hash,
            checks=checks,
            details=details,
            error_message=error_msg,
            expires_at=expires_at,
            created_at=now,
            is_expired=False,
        )

        return result_dto

    async def _preflight_printer(
        self,
        snapshot: TicketSnapshot,
        params: Dict[str, Any],
        scenario_key: str,
    ) -> Tuple[str, List[PreflightCheckDTO], Dict[str, Any]]:
        checks: List[PreflightCheckDTO] = []
        details: Dict[str, Any] = {}
        is_all_ok = True

        target_host = params.get("pc_name") or get_fact(snapshot, "pc_name")
        if not target_host:
            checks.append(PreflightCheckDTO(name="pc_name", status="failed", message="Не указано имя ПК"))
            return "failed", checks, details

        # 1. DNS check on host
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(loop.run_in_executor(None, socket.gethostbyname, target_host), timeout=2.0)
            checks.append(PreflightCheckDTO(name="dns_resolution", status="passed", details={"host": target_host}))
        except Exception:
            checks.append(PreflightCheckDTO(name="dns_resolution", status="failed", details={"host": target_host}, message="Имя ПК не резолвится через DNS"))
            is_all_ok = False

        # 2. Port checks: 5985 (WinRM) & 445 (SMB)
        try:
            smb_ok = await FastSocketProbe.probe(target_host, 445, timeout=1.5)
            winrm_ok = await FastSocketProbe.probe(target_host, 5985, timeout=1.5)
            details["host_ports"] = {"445": smb_ok, "5985": winrm_ok}

            if not smb_ok and not winrm_ok:
                checks.append(PreflightCheckDTO(name="host_reachability", status="failed", details=details["host_ports"], message="ПК недоступен по портам WinRM (5985) и SMB (445)"))
                is_all_ok = False
            else:
                checks.append(PreflightCheckDTO(name="host_reachability", status="passed", details=details["host_ports"]))
        except Exception as exc:
            checks.append(PreflightCheckDTO(name="host_reachability", status="warning", message=f"Сетевой зонд хоста вернул ошибку: {exc}"))

        # 3. Network printer check (Port 9100) if printer_address specified
        printer_address = params.get("printer_address") or get_fact(snapshot, "printer_address")
        if printer_address:
            try:
                p9100_ok = await FastSocketProbe.probe(printer_address, 9100, timeout=1.5)
                details["printer_p9100"] = p9100_ok
                if p9100_ok:
                    checks.append(PreflightCheckDTO(name="printer_port_9100", status="passed", details={"address": printer_address}))
                else:
                    checks.append(PreflightCheckDTO(name="printer_port_9100", status="warning", details={"address": printer_address}, message="Принтер не отвечает по порту 9100 (RAW)"))
            except Exception:
                checks.append(PreflightCheckDTO(name="printer_port_9100", status="warning", details={"address": printer_address}, message="Сетевой зонд принтера не ответил"))

        status = "passed" if is_all_ok else "failed"
        return status, checks, details

    async def _preflight_offline_host(
        self,
        snapshot: TicketSnapshot,
        params: Dict[str, Any],
    ) -> Tuple[str, List[PreflightCheckDTO], Dict[str, Any]]:
        checks: List[PreflightCheckDTO] = []
        details: Dict[str, Any] = {}

        target_host = params.get("pc_name") or get_fact(snapshot, "pc_name")
        if not target_host:
            checks.append(PreflightCheckDTO(name="pc_name", status="failed", message="Не указано имя ПК"))
            return "failed", checks, details

        # For offline_host: probe host availability as diagnostics, but offline is NOT an error!
        try:
            smb_ok = await FastSocketProbe.probe(target_host, 445, timeout=1.0)
            winrm_ok = await FastSocketProbe.probe(target_host, 5985, timeout=1.0)
            details["is_online"] = bool(smb_ok or winrm_ok)
            details["ports"] = {"445": smb_ok, "5985": winrm_ok}
            checks.append(PreflightCheckDTO(
                name="offline_host_diagnostic",
                status="passed",
                details=details,
                message=f"Диагностика ПК {target_host}: {'Online' if details['is_online'] else 'Offline'}",
            ))
        except Exception as exc:
            details["probe_error"] = str(exc)
            checks.append(PreflightCheckDTO(name="offline_host_diagnostic", status="passed", details=details))

        return "passed", checks, details

    async def _preflight_ad_user(
        self,
        snapshot: TicketSnapshot,
        params: Dict[str, Any],
        scenario_key: str,
    ) -> Tuple[str, List[PreflightCheckDTO], Dict[str, Any]]:
        checks: List[PreflightCheckDTO] = []
        details: Dict[str, Any] = {}

        target_user = params.get("target_user") or get_fact(snapshot, "target_user") or params.get("user_name") or get_fact(snapshot, "user_name")
        if not target_user:
            checks.append(PreflightCheckDTO(name="target_user", status="failed", message="Не указан логин пользователя"))
            return "failed", checks, details

        escaped = escape_filter_chars(target_user.strip())
        pool = self._get_ad_pool()

        # Run LDAP search in executor
        def _search_user() -> Tuple[int, Optional[str], Optional[Dict[str, Any]]]:
            with pool.connection_scope(auto_bind=True, read_only=True) as conn:
                domain = pool.config.domain
                base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)
                search_filter = f"(&(objectClass=user)(|(sAMAccountName={escaped})(userPrincipalName={escaped}*)(cn={escaped})))"
                conn.search(
                    search_base=base_dn,
                    search_filter=search_filter,
                    search_scope=ldap3.SUBTREE,
                    attributes=["distinguishedName", "sAMAccountName", "userAccountControl", "memberOf"],
                )
                entries = conn.entries or []
                if len(entries) == 1:
                    e = entries[0]
                    user_dn = str(getattr(e, "distinguishedName", ""))
                    member_of = [str(g) for g in getattr(e, "memberOf", [])]
                    uac = getattr(e, "userAccountControl", None)
                    return 1, user_dn, {"member_of": member_of, "uac": str(uac)}
                return len(entries), None, None

        loop = asyncio.get_running_loop()
        try:
            count, user_dn, user_meta = await asyncio.wait_for(loop.run_in_executor(None, _search_user), timeout=3.0)
        except Exception as exc:
            logger.warning("AD lookup failed during preflight for '%s': %s", target_user, exc)
            checks.append(PreflightCheckDTO(name="ad_ldap_search", status="warning", message=f"Active Directory недоступен: {type(exc).__name__}"))
            return "degraded", checks, details

        if count == 0:
            checks.append(PreflightCheckDTO(name="ad_user_exists", status="failed", message=f"Пользователь '{target_user}' не найден в Active Directory"))
            return "failed", checks, details
        elif count > 1:
            checks.append(PreflightCheckDTO(name="ad_user_exists", status="failed", message=f"Найдено несколько ({count}) учетных записей для '{target_user}' в Active Directory"))
            return "failed", checks, details

        details["user_dn"] = user_dn
        checks.append(PreflightCheckDTO(name="ad_user_exists", status="passed", details={"user_dn": user_dn}))

        if scenario_key == "grant_wlan":
            member_of = (user_meta or {}).get("member_of", [])
            is_member = any("WLAN" in g.upper() for g in member_of)
            details["already_in_wlan_group"] = is_member
            checks.append(PreflightCheckDTO(
                name="wlan_group_membership",
                status="passed",
                details={"already_member": is_member},
                message="Пользователь уже состоит в группе WLAN" if is_member else "Готов к добавлению в группу WLAN",
            ))

        return "passed", checks, details

    async def _preflight_account_create(
        self,
        snapshot: TicketSnapshot,
        params: Dict[str, Any],
    ) -> Tuple[str, List[PreflightCheckDTO], Dict[str, Any]]:
        checks: List[PreflightCheckDTO] = []
        details: Dict[str, Any] = {}

        first_name = params.get("first_name") or get_fact(snapshot, "first_name")
        last_name = params.get("last_name") or get_fact(snapshot, "last_name")
        middle_name = params.get("middle_name") or get_fact(snapshot, "middle_name") or ""

        if not first_name or not last_name:
            user_name = params.get("user_name") or get_fact(snapshot, "user_name")
            if user_name:
                parts = user_name.split()
                if len(parts) >= 2:
                    last_name = last_name or parts[0]
                    first_name = first_name or parts[1]
                    if len(parts) >= 3:
                        middle_name = middle_name or parts[2]

        if not first_name or not last_name:
            checks.append(PreflightCheckDTO(name="identity_names", status="failed", message="Не указаны Фамилия и Имя сотрудника"))
            return "failed", checks, details

        candidate_sam = generate_sam_account_name(last_name, first_name, middle_name, collision_index=1)
        details["candidate_sam"] = candidate_sam
        checks.append(PreflightCheckDTO(name="sam_generation", status="passed", details={"candidate_sam": candidate_sam}))

        # Read-only conflict check in AD
        pool = self._get_ad_pool()
        escaped = escape_filter_chars(candidate_sam)

        def _check_collision() -> bool:
            with pool.connection_scope(auto_bind=True, read_only=True) as conn:
                domain = pool.config.domain
                base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)
                conn.search(
                    search_base=base_dn,
                    search_filter=f"(&(objectClass=user)(sAMAccountName={escaped}))",
                    search_scope=ldap3.SUBTREE,
                    attributes=["sAMAccountName"],
                )
                return bool(conn.entries)

        loop = asyncio.get_running_loop()
        try:
            has_collision = await asyncio.wait_for(loop.run_in_executor(None, _check_collision), timeout=3.0)
            if has_collision:
                checks.append(PreflightCheckDTO(name="ad_sam_collision", status="failed", message=f"Логин '{candidate_sam}' уже занят в Active Directory"))
                return "failed", checks, details
            checks.append(PreflightCheckDTO(name="ad_sam_collision", status="passed", message=f"Логин '{candidate_sam}' свободен"))
        except Exception as exc:
            logger.warning("AD collision check failed during preflight: %s", exc)
            checks.append(PreflightCheckDTO(name="ad_sam_collision", status="warning", message="Проверка коллизий AD недоступна"))
            return "degraded", checks, details

        return "passed", checks, details

    async def _preflight_rag(
        self,
        snapshot: TicketSnapshot,
        session: Optional[AsyncSession],
    ) -> Tuple[str, List[PreflightCheckDTO], Dict[str, Any]]:
        checks: List[PreflightCheckDTO] = []
        details: Dict[str, Any] = {}

        if session is not None:
            try:
                from core.database.models import TaskKnowledgeBase

                stmt = select(TaskKnowledgeBase.task_id).where(TaskKnowledgeBase.service_id == snapshot.service_id).limit(1)
                res = await session.execute(stmt)
                has_kb = res.scalar_one_or_none() is not None
                details["has_service_kb"] = has_kb
                checks.append(PreflightCheckDTO(name="kb_availability", status="passed", details={"has_service_kb": has_kb}))
            except Exception as exc:
                checks.append(PreflightCheckDTO(name="kb_availability", status="warning", message=str(exc)))
        else:
            checks.append(PreflightCheckDTO(name="kb_availability", status="passed"))

        return "passed", checks, details
