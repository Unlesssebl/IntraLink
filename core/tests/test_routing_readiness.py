"""Unit tests for FactReadinessPolicy in Evidence-Based Routing Cascade."""

from core.routing.contracts import RoutingState, TicketSnapshot
from core.routing.readiness import FactReadinessPolicy


def _make_snapshot(
    task_id: int = 142000,
    title: str = "Test ticket",
    description: str = "Test description",
    entities: dict[str, str] | None = None,
    custom_fields: dict[str, str] | None = None,
) -> TicketSnapshot:
    return TicketSnapshot(
        task_id=task_id,
        status_id=1,
        title=title,
        description=description,
        entities=entities or {},
        custom_fields=custom_fields or {},
        snapshot_hash="0" * 64,
    )


def test_install_printer_case1_usb_only_needs_pc_name():
    """Case 1 (USB): Requires only pc_name; address is NOT required."""
    policy = FactReadinessPolicy()
    # USB with pc_name -> selected
    snap_ok = _make_snapshot(entities={"pc_name": "PC-001", "printer_connection_type": "USB"})
    assert policy.check_readiness("install_printer", snap_ok) == (RoutingState.selected, [])

    # USB in text with pc_name -> selected
    snap_text = _make_snapshot(title="Установить локальный принтер через юсб шнур", entities={"pc_name": "PC-001"})
    assert policy.check_readiness("install_printer", snap_text) == (RoutingState.selected, [])

    # USB without pc_name -> needs_clarification with pc_name only
    snap_no_pc = _make_snapshot(entities={"printer_connection_type": "USB"})
    assert policy.check_readiness("install_printer", snap_no_pc) == (RoutingState.needs_clarification, ["pc_name"])


def test_install_printer_case2_explicit_network_without_address():
    """Case 2 (Explicit network/ethernet without address): missing_facts = ['printer_address']."""
    policy = FactReadinessPolicy()
    # Explicit network in connection_type entity
    snap_net = _make_snapshot(entities={"pc_name": "PC-001", "printer_connection_type": "network"})
    state, missing = policy.check_readiness("install_printer", snap_net)
    assert state == RoutingState.needs_clarification
    assert missing == ["printer_address"]

    # Explicit ethernet in title
    snap_title = _make_snapshot(title="Подключить сетевой принтер", entities={"pc_name": "PC-001"})
    state, missing = policy.check_readiness("install_printer", snap_title)
    assert state == RoutingState.needs_clarification
    assert missing == ["printer_address"]


def test_install_printer_case3_unknown_connection_type_without_address():
    """Case 3 (Unknown connection type without address): missing_facts = ['printer_address', 'printer_connection_type']."""
    policy = FactReadinessPolicy()
    snap = _make_snapshot(title="Установить принтер Kyocera", description="В кабинете 305", entities={"pc_name": "PC-001"})
    state, missing = policy.check_readiness("install_printer", snap)
    assert state == RoutingState.needs_clarification
    assert missing == ["printer_address", "printer_connection_type"]


def test_install_printer_case4_address_without_connection_type_sufficient():
    """Case 4 (Address without connection type): Sufficient for network branch -> selected."""
    policy = FactReadinessPolicy()
    # Has pc_name and printer_address, but printer_connection_type is empty/unknown
    snap = _make_snapshot(
        title="Установить принтер",
        description="В кабинете 305",
        entities={"pc_name": "PC-001", "printer_address": "10.244.10.50"},
    )
    assert policy.check_readiness("install_printer", snap) == (RoutingState.selected, [])


def test_printer_spooler_restart_and_offline_host():
    policy = FactReadinessPolicy()
    # Missing pc_name
    snapshot_empty = _make_snapshot(entities={})
    state, missing = policy.check_readiness("printer_spooler_restart", snapshot_empty)
    assert state == RoutingState.needs_clarification
    assert missing == ["pc_name"]

    state_off, missing_off = policy.check_readiness("offline_host", snapshot_empty)
    assert state_off == RoutingState.needs_clarification
    assert missing_off == ["pc_name"]

    # Populated pc_name
    snapshot_ok = _make_snapshot(entities={"pc_name": "WKS-999"})
    assert policy.check_readiness("printer_spooler_restart", snapshot_ok) == (RoutingState.selected, [])
    assert policy.check_readiness("offline_host", snapshot_ok) == (RoutingState.selected, [])


def test_default_printer_fix_requirements():
    policy = FactReadinessPolicy()

    # Missing pc_name and target
    snap1 = _make_snapshot(entities={})
    state1, missing1 = policy.check_readiness("default_printer_fix", snap1)
    assert state1 == RoutingState.needs_clarification
    assert missing1 == ["pc_name", "printer_address"]

    # Has pc_name, has printer_address
    snap2 = _make_snapshot(entities={"pc_name": "PC-01", "printer_address": "10.244.1.20"})
    assert policy.check_readiness("default_printer_fix", snap2) == (RoutingState.selected, [])

    # Has pc_name, has printer_model (alternative to address)
    snap3 = _make_snapshot(entities={"pc_name": "PC-01", "printer_model": "HP LaserJet P2035"})
    assert policy.check_readiness("default_printer_fix", snap3) == (RoutingState.selected, [])


def test_grant_wlan_and_account_lock_canonical_target_user():
    policy = FactReadinessPolicy()

    # Both empty -> canonical missing target_user
    snap_empty = _make_snapshot(entities={})
    state, missing = policy.check_readiness("grant_wlan", snap_empty)
    assert state == RoutingState.needs_clarification
    assert missing == ["target_user"]

    state_lock, missing_lock = policy.check_readiness("account_lock", snap_empty)
    assert state_lock == RoutingState.needs_clarification
    assert missing_lock == ["target_user"]

    # Populated target_user
    snap_user = _make_snapshot(entities={"target_user": "ivanov.i"})
    assert policy.check_readiness("grant_wlan", snap_user) == (RoutingState.selected, [])
    assert policy.check_readiness("account_lock", snap_user) == (RoutingState.selected, [])

    # Populated user_name as alternative
    snap_alt = _make_snapshot(entities={"user_name": "Иванов Иван"})
    assert policy.check_readiness("grant_wlan", snap_alt) == (RoutingState.selected, [])
    assert policy.check_readiness("account_lock", snap_alt) == (RoutingState.selected, [])


def test_account_create_fact_rules():
    policy = FactReadinessPolicy()

    # Missing all
    snap_empty = _make_snapshot(entities={})
    state, missing = policy.check_readiness("account_create", snap_empty)
    assert state == RoutingState.needs_clarification
    assert missing == ["department", "first_name", "last_name", "title"]

    # Multi-part user_name fallback + department + title
    snap_name = _make_snapshot(
        entities={"user_name": "Петров Петр", "department": "Бухгалтерия", "title": "Экономист"}
    )
    assert policy.check_readiness("account_create", snap_name) == (RoutingState.selected, [])

    # Single-part user_name is insufficient
    snap_single = _make_snapshot(
        entities={"user_name": "Петров", "department": "Бухгалтерия", "title": "Экономист"}
    )
    state, missing = policy.check_readiness("account_create", snap_single)
    assert state == RoutingState.needs_clarification
    assert "first_name" in missing


def test_service_redirect_and_rag_consultation_no_required_facts():
    policy = FactReadinessPolicy()
    snap = _make_snapshot(entities={})
    assert policy.check_readiness("service_redirect", snap) == (RoutingState.selected, [])
    assert policy.check_readiness("rag_consultation", snap) == (RoutingState.selected, [])


def test_placeholders_treated_as_empty():
    policy = FactReadinessPolicy()
    for ph in ("-", "—", "нет", "не указано", "n/a", "none", "null", "   "):
        snap = _make_snapshot(entities={"pc_name": ph, "printer_address": ph})
        state, missing = policy.check_readiness("install_printer", snap)
        assert state == RoutingState.needs_clarification
        assert "pc_name" in missing
