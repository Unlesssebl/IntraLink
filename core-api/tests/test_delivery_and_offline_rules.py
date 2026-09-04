import pytest
from app.services.ai_synthesis import extract_thread_context
from app.services.rules.base import RuleDecision
from app.services.rules.engine import RuleEngine
from app.services.rules.offline_host import OfflineHostRule
from app.services.rules.physical_device import PhysicalDeliveryRule
from app.services.template_engine import auto_detect_template


def test_extract_thread_context_intraservice_keys():
    """Проверка извлечения контекста с реальными ключами API IntraService (Comments, Editor, Date)."""
    raw_history = [
        {
            "Comments": "находится в 112 кабинете",
            "IsPublic": True,
            "Date": "2026-06-15T08:49:34.703",
            "EditorId": 8009,
            "Editor": "Черкасова Наталья",
        },
        {
            "Date": "2026-06-15T08:41:26.71",
            "EditorId": 8009,
            "Editor": "Черкасова Наталья",
            "ExecutorsGroup": "1 линия",
            "Files": "",
            "Participants": "Зиннатуллин Ильнар, Девитьяров Владислав",
            "StatusId": 31,
        },
    ]

    ctx = extract_thread_context(raw_history)
    assert ctx["is_follow_up"] is True
    assert ctx["last_author"] == "Черкасова Наталья"
    assert ctx["last_comment"] == "находится в 112 кабинете"
    assert len(ctx["thread"]) == 1


def test_physical_delivery_rule_initial_request():
    """Если ПК не включается, но его еще не принесли -> статус 48 (Ожидание устройства)."""
    rule = PhysicalDeliveryRule()
    task = {
        "Id": 133328,
        "Name": "акт технического освидетельствования",
        "Description": "не включается",
        "ServiceId": 32,
    }

    decision = rule.evaluate(task=task, diag={"is_online": False, "target": "NTEMW0237"})
    assert decision is not None
    assert decision.status_id == 48
    assert decision.template_key == "hardware_repair"
    assert "Приносите системный блок" in decision.comment


def test_physical_delivery_rule_delivered_comment():
    """Если в комментарии указано, что ПК в 112 кабинете -> статус 27 (В работе)."""
    rule = PhysicalDeliveryRule()
    task = {
        "Id": 133328,
        "Name": "акт технического освидетельствования",
        "Description": "не включается",
        "ServiceId": 32,
    }
    history = [
        {
            "Comments": "находится в 112 кабинете",
            "Date": "2026-06-15T08:49:34.703",
            "Editor": "Черкасова Наталья",
        }
    ]

    decision = rule.evaluate(
        task=task,
        diag={"is_online": False, "target": "NTEMW0237"},
        context={"comments_history": history},
    )
    assert decision is not None
    assert decision.status_id == 27
    assert decision.status_name == "В работе"
    assert decision.template_key == "device_delivered_in_work"
    assert "принят в 112 кабинете" in decision.comment.lower()
    assert "приступаю к работе" in decision.comment.lower()


def test_offline_host_rule_bypassed_for_hardware_and_delivery():
    """OfflineHostRule не должно срабатывать, если ПК не включается или уже принесен."""
    rule = OfflineHostRule()
    diag = {"is_online": False, "target": "NTEMW0237"}

    # Кейс 1: Аппаратная поломка в описании
    task_hw = {
        "Id": 133328,
        "Name": "акт технического освидетельствования",
        "Description": "не включается",
    }
    assert rule.evaluate(task=task_hw, diag=diag) is None

    # Кейс 2: Доставка в 112 кабинете в истории
    task_delivery = {
        "Id": 133328,
        "Name": "заявка на ПК",
        "Description": "диагностика",
    }
    history = [{"Comments": "принес системник в 112", "Editor": "Иванов И.И."}]
    assert rule.evaluate(task=task_delivery, diag=diag, context={"comments_history": history}) is None

    # Кейс 3: Обычная прикладная заявка без поломки железа -> срабатывает pc_offline (статус 35)
    task_normal = {
        "Id": 140001,
        "Name": "Не открывается программа",
        "Description": "Ошибка подключения к базе",
    }
    normal_dec = rule.evaluate(task=task_normal, diag=diag)
    assert normal_dec is not None
    assert normal_dec.status_id == 35
    assert normal_dec.template_key == "pc_offline"


def test_auto_detect_template_end_to_end():
    """Проверка полного контура auto_detect_template для кейса #133328."""
    task = {
        "Id": 133328,
        "Name": "акт технического освидетельствования",
        "Description": "не включается",
        "ServiceId": 32,
        "_field_meta": {"pc_name": "NTEMW0237"},
    }
    history = [
        {
            "Comments": "находится в 112 кабинете",
            "Date": "2026-06-15T08:49:34.703",
            "Editor": "Черкасова Наталья",
        }
    ]
    diag = {"is_online": False, "target": "NTEMW0237"}

    decision = auto_detect_template(
        task=task,
        diag=diag,
        comments_history=history,
    )
    assert decision["status_id"] == 27
    assert decision["template_key"] == "device_delivered_in_work"
    assert "принят в 112 кабинете" in decision["comment"].lower()
