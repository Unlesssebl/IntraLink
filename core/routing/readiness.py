"""Pure fact completeness validation for Evidence-Based Routing Cascade.

Evaluates whether required parameters for a selected scenario are present in the
ticket snapshot without performing any network I/O, LDAP lookups, or live preflight probes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from core.routing.contracts import RoutingState, TicketSnapshot

EMPTY_PLACEHOLDERS = frozenset({
    "",
    "-",
    "—",
    "нет",
    "не указано",
    "не указан",
    "n/a",
    "na",
    "none",
    "null",
})

USB_KEYWORDS = (
    "usb",
    "юсб",
    "шнур",
    "кабел",
    "провод",
    "локальн",
)


def is_empty_value(val: str | None) -> bool:
    """Return True if string is None, empty, whitespace, or a known placeholder."""
    if val is None:
        return True
    cleaned = str(val).strip().lower()
    return not cleaned or cleaned in EMPTY_PLACEHOLDERS


def get_fact(snapshot: TicketSnapshot, key: str) -> str | None:
    """Retrieve an extracted entity or custom field value by key (ignoring placeholders)."""
    # 1. Check snapshot.entities
    val = snapshot.entities.get(key)
    if val is not None and not is_empty_value(val):
        return val.strip()

    # 2. Check snapshot.custom_fields (case-insensitive key match)
    key_lower = key.lower()
    for cf_k, cf_v in snapshot.custom_fields.items():
        if cf_k.strip().lower() == key_lower and cf_v is not None and not is_empty_value(cf_v):
            return str(cf_v).strip()

    return None


class FactReadinessPolicy:
    """Evaluates fact completeness for selected scenarios without external I/O."""

    def check_readiness(
        self,
        scenario_key: str,
        snapshot: TicketSnapshot,
    ) -> tuple[RoutingState, Sequence[str]]:
        """Validate presence of all mandatory facts for the scenario.

        Returns:
            Tuple of (RoutingState.selected, []) if all required facts are present,
            or (RoutingState.needs_clarification, sorted_missing_facts) otherwise.
        """
        missing: list[str] = []

        if scenario_key == "install_printer":
            pc_name = get_fact(snapshot, "pc_name")
            if not pc_name:
                missing.append("pc_name")

            conn_type = get_fact(snapshot, "printer_connection_type")
            text_corpus = f"{snapshot.title} {snapshot.description}".lower()

            is_usb = (
                (conn_type is not None and any(kw in conn_type.lower() for kw in ("usb", "юсб", "локальн")))
                or any(re.search(rf"\b{re.escape(kw)}\b", text_corpus) for kw in ("usb", "юсб"))
                or any(kw in text_corpus for kw in ("шнур", "кабел", "провод", "локальн"))
            )

            if is_usb:
                # USB connection requires only pc_name
                pass
            else:
                printer_addr = get_fact(snapshot, "printer_address")
                if printer_addr:
                    # Address provided without connection type is sufficient for network branch
                    pass
                else:
                    is_explicit_network = (
                        (
                            conn_type is not None
                            and any(kw in conn_type.lower() for kw in ("network", "ethernet", "сетевой", "ip", "lan", "tcp"))
                        )
                        or any(re.search(rf"\b{re.escape(kw)}\b", text_corpus) for kw in ("сетевой", "ethernet", "network", "lan", "ip"))
                    )
                    if is_explicit_network:
                        # Explicit network/ethernet connection missing address
                        missing.append("printer_address")
                    else:
                        # Unknown connection type and missing printer address
                        missing.append("printer_address")
                        missing.append("printer_connection_type")

        elif scenario_key in ("printer_spooler_restart", "offline_host"):
            pc_name = get_fact(snapshot, "pc_name")
            if not pc_name:
                missing.append("pc_name")

        elif scenario_key == "default_printer_fix":
            pc_name = get_fact(snapshot, "pc_name")
            if not pc_name:
                missing.append("pc_name")

            printer_addr = get_fact(snapshot, "printer_address")
            printer_model = get_fact(snapshot, "printer_model")
            if not printer_addr and not printer_model:
                missing.append("printer_address")

        elif scenario_key in ("grant_wlan", "account_lock"):
            target_user = get_fact(snapshot, "target_user")
            user_name = get_fact(snapshot, "user_name")
            if not target_user and not user_name:
                missing.append("target_user")

        elif scenario_key == "account_create":
            first_name = get_fact(snapshot, "first_name")
            last_name = get_fact(snapshot, "last_name")
            user_name = get_fact(snapshot, "user_name")

            has_valid_name = bool(first_name and last_name)
            if not has_valid_name and user_name:
                parts = [p for p in user_name.split() if not is_empty_value(p)]
                if len(parts) >= 2:
                    has_valid_name = True

            if not has_valid_name:
                if not last_name:
                    missing.append("last_name")
                if not first_name:
                    missing.append("first_name")

            department = get_fact(snapshot, "department")
            if not department:
                missing.append("department")

            # For title (job position), check entity or custom field
            job_title = snapshot.entities.get("title")
            if is_empty_value(job_title):
                job_title = None
                for cf_k, cf_v in snapshot.custom_fields.items():
                    if (
                        cf_k.strip().lower() in ("title", "должность", "job_title", "position")
                        and cf_v
                        and not is_empty_value(cf_v)
                    ):
                        job_title = str(cf_v).strip()
                        break

            if not job_title:
                missing.append("title")

        elif scenario_key in ("service_redirect", "rag_consultation"):
            pass

        else:
            # Other scenarios don't enforce mandatory static facts
            pass

        sorted_missing = sorted(set(missing))
        if sorted_missing:
            return RoutingState.needs_clarification, sorted_missing

        return RoutingState.selected, []
