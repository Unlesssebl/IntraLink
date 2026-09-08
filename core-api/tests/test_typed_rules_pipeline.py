from app.services.rules.engine import RuleEngine
from app.services.rules.printers import PrinterRule
from app.services.rules.offline_host import OfflineHostRule
from app.services.rules.physical_device import PhysicalDeliveryRule
from app.services.rules.redirect import ServiceRedirectRule
from app.services.rules.file_locks import FileLockRule
from app.services.rules.remote_access import RemoteAccessRule
from app.services.rules.rag_consensus import RAGConsensusRule
from shared.domain import ClarificationRequired, ResolutionProposed, NoMatch


def test_printer_rule_evaluate_typed():
    rule = PrinterRule()
    # 1. Offline MFU
    diag_mfu_off = {"target": "PRN-MFU-01", "is_online": False}
    outcome = rule.evaluate_typed(
        task={"Name": "Не работает принтер", "Description": "Kyocera не печатает"},
        diag=diag_mfu_off,
    )
    assert isinstance(outcome, ClarificationRequired)
    assert outcome.outcome_key == "printer_offline"

    # 2. Online PC, missing IP
    diag_pc_on = {"target": "NTEMW0144", "is_online": True}
    outcome2 = rule.evaluate_typed(
        task={"Name": "Настроить принтер", "Description": "Подключить принтер в бухгалтерию"},
        diag=diag_pc_on,
    )
    assert isinstance(outcome2, ClarificationRequired)
    assert outcome2.outcome_key == "printer_ip_clarify"


def test_offline_host_strict_jinja_guard():
    rule = OfflineHostRule()
    # PC offline but name is UNKNOWN -> NoMatch (Strict Jinja guard prevents missing variable error)
    diag_unknown = {"target": "UNKNOWN", "is_online": False}
    outcome = rule.evaluate_typed(
        task={"Name": "Проблема с ПК", "Description": "Не открывается браузер"},
        diag=diag_unknown,
    )
    assert isinstance(outcome, NoMatch)

    # PC offline and name is valid -> ClarificationRequired with pc_name in context
    diag_valid = {"target": "NTEMW0144", "is_online": False}
    outcome_valid = rule.evaluate_typed(
        task={"Name": "Проблема с ПК", "Description": "Не открывается браузер"},
        diag=diag_valid,
    )
    assert isinstance(outcome_valid, ClarificationRequired)
    assert outcome_valid.outcome_key == "pc_offline"
    assert outcome_valid.context.get("pc_name") == "NTEMW0144"


def test_physical_device_delivery_typed():
    rule = PhysicalDeliveryRule()
    # 1. Already delivered
    outcome = rule.evaluate_typed(
        task={"Name": "Ремонт ПК", "Description": "Системный блок"},
        context={"comments_history": [{"Comments": "Принес системник в 112 каб"}]},
    )
    assert isinstance(outcome, ResolutionProposed)
    assert outcome.outcome_key == "device_delivered_in_work"
    assert outcome.target_status_id == 27

    # 2. Hardware issue requesting bring-in
    outcome2 = rule.evaluate_typed(
        task={"Name": "Сломался ПК", "Description": "Черный экран, замена видеокарты"},
    )
    assert isinstance(outcome2, ResolutionProposed)
    assert outcome2.target_status_id == 48


def test_service_redirect_typed():
    rule = ServiceRedirectRule()
    outcome = rule.evaluate_typed(
        task={
            "Name": "Ошибка 1С:УПП",
            "Description": "Не проводится документ реализации в 1С",
            "ServiceId": 1,
            "ServiceParentId": 1,
        },
    )
    assert isinstance(outcome, ResolutionProposed)
    assert outcome.outcome_key == "wrong_service"
    assert outcome.target_status_id == 30
    assert outcome.metadata.get("is_redirect") is True
    assert "target_service" in outcome.context


def test_file_lock_rule_typed():
    rule = FileLockRule()
    # 1. No path
    outcome = rule.evaluate_typed(
        task={"Name": "Заблокирован файл", "Description": "Файл занят другим пользователем"},
    )
    assert isinstance(outcome, ClarificationRequired)
    assert outcome.outcome_key == "file_lock_smb"

    # 2. Path provided
    outcome2 = rule.evaluate_typed(
        task={"Name": "Заблокирован файл", "Description": "Файл занят: \\\\srv-fs01\\share\\doc.xlsx"},
    )
    assert isinstance(outcome2, ResolutionProposed)
    assert outcome2.outcome_key == "file_lock_smb_in_progress"
    assert outcome2.target_status_id == 27


def test_remote_access_rule_typed():
    rule = RemoteAccessRule()
    outcome = rule.evaluate_typed(
        task={"Name": "AnyDesk сбой", "Description": "AnyDesk не подключается, ошибка сети"},
        diag={"is_online": True},
    )
    assert isinstance(outcome, ClarificationRequired)
    assert outcome.outcome_key == "anydesk_fallback_assistant"


def test_rag_consensus_rule_typed():
    rule = RAGConsensusRule()
    top_match = {
        "task_id": 88888,
        "similarity_pct": 94.5,
        "solution": "Установлен корневой сертификат Минцифры",
        "status_name": "Выполнена",
    }
    outcome = rule.evaluate_typed(
        task={"Name": "Установка сертификата", "Description": "Прошу установить сертификат"},
        kb_matches=[top_match],
    )
    assert isinstance(outcome, ResolutionProposed)
    assert outcome.outcome_key == "rag_historical_solution"
    assert outcome.context.get("solution") == "Установлен корневой сертификат Минцифры"
    assert outcome.metadata.get("rag_applied") is True


def test_rule_engine_downtime_safety_guard():
    engine = RuleEngine()
    # Task contains downtime keyword "остановка производства" -> MUST return downtime_priority with status 27
    task = {
        "Name": "Срочно! Остановка производства на линии упаковки",
        "Description": "Не печатает этикетки, простой смены",
        "ServiceId": 1,
    }
    outcome = engine.evaluate_typed(task)
    assert isinstance(outcome, ResolutionProposed)
    assert outcome.outcome_key == "downtime_priority"
    assert outcome.target_status_id == 27
    assert outcome.metadata.get("risk_level") == "critical"
    assert "остановка производства" in outcome.metadata.get("trigger_markers", [])
