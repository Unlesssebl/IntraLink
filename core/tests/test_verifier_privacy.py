"""Tests for VerifierPrivacyRouter, Free-Text DLP, and Privacy Zones."""

from core.routing.contracts import (
    ScenarioCandidate,
    SnapshotComment,
    TicketSnapshot,
)
from core.routing.verifier.contracts import PrivacyZone
from core.routing.verifier.privacy import (
    SANITIZER_VERSION,
    VerifierPrivacyRouter,
)


def _make_snapshot(
    title: str = "Обычный запрос",
    description: str = "Описание",
    comments: list[str] | None = None,
    entities: dict[str, str] | None = None,
    custom_fields: dict[str, str] | None = None,
    service_id: int = 12,
    service_name: str = "Рабочие места",
) -> TicketSnapshot:
    pub_comments = []
    for idx, c in enumerate(comments or []):
        pub_comments.append(
            SnapshotComment(
                id=idx + 1,
                text=c,
                author_name="User",
                is_private=False,
            )
        )

    return TicketSnapshot(
        task_id=12345,
        status_id=1,
        service_id=service_id,
        service_name=service_name,
        title=title,
        description=description,
        public_comments=pub_comments,
        custom_fields=custom_fields or {"field101": "some_value"},
        entities=entities or {},
        snapshot_hash="f" * 64,
    )


def test_custom_fields_and_entities_not_in_prepared_input():
    """DoD: custom_fields and entities are strictly excluded from PreparedVerifierInput."""
    router = VerifierPrivacyRouter()
    snapshot = _make_snapshot(
        entities={"user_name": "Иванов Иван", "pc_name": "WKS-100"},
        custom_fields={"1015": "SecretCustomFieldData"},
    )
    candidates = [
        ScenarioCandidate(scenario_key="install_printer", scenario_version="1.0.0")
    ]
    prepared = router.prepare(snapshot, candidates)

    # PreparedVerifierInput schema must not have custom_fields or entities
    assert not hasattr(prepared, "custom_fields")
    assert not hasattr(prepared, "entities")
    assert not hasattr(prepared, "attachments")
    assert prepared.sanitizer_version == SANITIZER_VERSION


def test_password_and_token_masked_red_zone():
    """Passwords and tokens must be masked and automatically trigger RED zone with helpdesk-local."""
    router = VerifierPrivacyRouter(cloud_model_alias="helpdesk-fast", local_model_alias="helpdesk-local")
    raw_desc = (
        "Пользователь сообщил временный пароль: VerySecretPass123!\n"
        "Служебный токен авторизации: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN\n"
        "Старый pwd: OldPassWord456"
    )
    snapshot = _make_snapshot(description=raw_desc)
    candidates = [
        ScenarioCandidate(scenario_key="install_printer", scenario_version="1.0.0")
    ]
    prepared = router.prepare(snapshot, candidates)

    assert "VerySecretPass123!" not in prepared.description
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN" not in prepared.description
    assert "OldPassWord456" not in prepared.description

    assert "[PASSWORD]" in prepared.description
    assert "[TOKEN]" in prepared.description

    assert prepared.privacy_zone == PrivacyZone.red
    assert prepared.requested_model_alias == "helpdesk-local"
    assert "PASSWORD" in prepared.detected_sensitive_types
    assert "TOKEN" in prepared.detected_sensitive_types


def test_email_and_phone_masked():
    """Emails and phone numbers must be masked with [EMAIL] and [PHONE]."""
    router = VerifierPrivacyRouter()
    snapshot = _make_snapshot(
        title="Заявка от ivanov@corp.local",
        description="Контактный номер заявителя +7 (999) 111-22-33 для связи.",
    )
    candidates = [
        ScenarioCandidate(scenario_key="install_printer", scenario_version="1.0.0")
    ]
    prepared = router.prepare(snapshot, candidates)

    assert "ivanov@corp.local" not in prepared.title
    assert "+7 (999) 111-22-33" not in prepared.description
    assert "[EMAIL]" in prepared.title
    assert "[PHONE]" in prepared.description


def test_internal_ip_and_hostname_masked():
    """Internal IPv4 addresses and workstation hostnames must be masked."""
    router = VerifierPrivacyRouter()
    snapshot = _make_snapshot(
        description="Не печатает на сетевом принтере с IP 192.168.1.150 или 10.20.30.40 на компьютере WKS-0099.corporate.loc",
    )
    candidates = [
        ScenarioCandidate(scenario_key="install_printer", scenario_version="1.0.0")
    ]
    prepared = router.prepare(snapshot, candidates)

    assert "192.168.1.150" not in prepared.description
    assert "10.20.30.40" not in prepared.description
    assert "WKS-0099.corporate.loc" not in prepared.description

    assert "[INTERNAL_IP]" in prepared.description
    assert "[HOST]" in prepared.description


def test_known_identity_values_from_entities_masked():
    """Known identity values from snapshot.entities must be masked."""
    router = VerifierPrivacyRouter()
    snapshot = _make_snapshot(
        title="Запрос на доступ для Сидорова",
        description="Сотрудник Сидоров Алексей Петрович просит доступ. Табельный номер 998877.",
        entities={
            "last_name": "Сидоров",
            "first_name": "Алексей",
            "middle_name": "Петрович",
            "tab_number": "998877",
            "pc_name": "MY-LAPTOP-55",
        },
    )
    candidates = [
        ScenarioCandidate(scenario_key="default_printer_fix", scenario_version="1.0.0")
    ]
    prepared = router.prepare(snapshot, candidates)

    assert "Сидоров" not in prepared.title
    assert "Сидоров Алексей Петрович" not in prepared.description
    assert "998877" not in prepared.description
    assert "[USER]" in prepared.title
    assert "[USER]" in prepared.description


def test_sensitive_scenarios_trigger_red_zone():
    """account_create, account_lock, and grant_wlan must unconditionally trigger RED zone."""
    router = VerifierPrivacyRouter(cloud_model_alias="helpdesk-fast", local_model_alias="helpdesk-local")

    for scen_key in ("account_create", "account_lock", "grant_wlan"):
        snapshot = _make_snapshot(title="Обычный текст без паролей", description="Простой запрос")
        candidates = [
            ScenarioCandidate(scenario_key=scen_key, scenario_version="1.0.0")
        ]
        prepared = router.prepare(snapshot, candidates)
        assert prepared.privacy_zone == PrivacyZone.red, f"Scenario {scen_key} did not trigger RED zone"
        assert prepared.requested_model_alias == "helpdesk-local"


def test_yellow_and_green_zones():
    """Clean technical request is GREEN; request with personal/infra tokens is YELLOW."""
    router = VerifierPrivacyRouter(cloud_model_alias="helpdesk-fast", local_model_alias="helpdesk-local")

    # GREEN: pure clean text without personal or infra credentials
    snapshot_clean = _make_snapshot(
        title="Зависла очередь печати",
        description="Документы не печатаются, принтер мигает желтым индикатором",
    )
    prep_green = router.prepare(
        snapshot_clean,
        [ScenarioCandidate(scenario_key="printer_spooler_restart", scenario_version="1.0.0")],
    )
    assert prep_green.privacy_zone == PrivacyZone.green
    assert prep_green.requested_model_alias == "helpdesk-fast"

    # YELLOW: contains phone or IP
    snapshot_yellow = _make_snapshot(
        title="Зависла печать",
        description="Мой телефон +7 (916) 123-45-67 для звонка",
    )
    prep_yellow = router.prepare(
        snapshot_yellow,
        [ScenarioCandidate(scenario_key="printer_spooler_restart", scenario_version="1.0.0")],
    )
    assert prep_yellow.privacy_zone == PrivacyZone.yellow
    assert prep_yellow.requested_model_alias == "helpdesk-fast"


def test_sanitized_input_hash_is_deterministic():
    """sanitized_input_hash must be strictly deterministic and reproducible."""
    router = VerifierPrivacyRouter()
    snapshot = _make_snapshot(
        title="Тест хеша",
        description="Описание для проверки стабильности",
    )
    candidates = [
        ScenarioCandidate(scenario_key="install_printer", scenario_version="1.0.0")
    ]
    prep1 = router.prepare(snapshot, candidates)
    prep2 = router.prepare(snapshot, candidates)

    assert prep1.sanitized_input_hash == prep2.sanitized_input_hash
    assert len(prep1.sanitized_input_hash) == 64
