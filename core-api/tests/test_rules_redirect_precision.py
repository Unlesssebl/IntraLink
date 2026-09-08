"""Тесты точности классификации ServiceRedirectRule и защиты от ложной отмены аварийных инцидентов."""

import pytest
from app.services.rules.redirect import ServiceRedirectRule, classify_target_service
from app.services.rules.engine import RuleEngine


@pytest.mark.asyncio
async def test_camera_network_issue_not_redirected_to_1c():
    """Кейс заявки #140411: отказ камер, коммутатора и простой весовой не должен редиректиться в 1С."""
    task = {
        "Id": 140411,
        "ServiceId": 20,  # 04. Проблемы с сетью и интернетом
        "ServiceName": "Общие вопросы (сеть)",
        "Name": "постоянное отключение камер",
        "Description": (
            "в последние дни часто начали притормаживать и отключаться камеры, "
            "помогает переподключение к коммутатору, сами восстанавливаются крайне редко. "
            "05.09.26 примерно с 22:50 начали все камеры (145, 146, 147, 168) сильно тормозить "
            "в том числе и 1С, а далее по очередь отключаться.\n\n"
            "Проблема с камерами носит постоянных характер, просим решить ее как можно скорее, "
            "этот тормозит производственный процесс, так как без камер не можем взвешивать машины, "
            "и происходит простой.\n\n"
            "в последний раз Мосягин и Влад разбирались. По утверждению Мосягина проблема на стороне сети, "
            "Влад проводя анализ работы сети выявлял пиковые повышения пинга."
        ),
    }

    # 1. Проверяем функцию семантической классификации
    target_root, reason = classify_target_service(f"{task['Name']}. {task['Description']}", task["ServiceId"])
    assert target_root == "04", f"Ожидался сетевой раздел 04, но получен {target_root} ({reason})"

    # 2. Проверяем правило ServiceRedirectRule
    rule = ServiceRedirectRule()
    decision = rule.evaluate(task)

    assert decision is not None
    # Заявка НЕ должна отменяться! Статус должен быть 27 («В работе»)
    assert decision.status_id == 27, f"Заявка с простоем получила статус {decision.status_id} вместо 27"
    assert decision.status_name == "В работе"
    assert not decision.is_redirect
    assert decision.risk_level == "critical"
    assert decision.template_key == "downtime_priority"
    assert "простой" in decision.trigger_markers or any(m in decision.trigger_markers for m in ["весы", "весовая"])
    assert decision.risk_warning is not None


@pytest.mark.asyncio
async def test_pure_1c_incident_redirected_to_1c():
    """Чистая заявка по 1С из неверного раздела должна перенаправляться в 06."""
    task = {
        "Id": 140412,
        "ServiceId": 16,  # 11. Общие вопросы
        "ServiceName": "Общие вопросы",
        "Name": "Ошибка закрытия месяца в 1С:УПП",
        "Description": "При проведении документа 'Реализация товаров' возникает ошибка блокировки таблиц в базе 1С.",
    }

    target_root, reason = classify_target_service(f"{task['Name']}. {task['Description']}", task["ServiceId"])
    assert target_root == "06"

    rule = ServiceRedirectRule()
    decision = rule.evaluate(task)

    assert decision is not None
    assert decision.is_redirect is True
    assert decision.status_id == 30
    assert decision.status_name == "Отменена"
    assert decision.target_root == "06"
    assert "06. Вопросы по 1С" in decision.name
    assert "1с" in decision.trigger_markers or "упп" in decision.trigger_markers


@pytest.mark.asyncio
async def test_directum_contract_redirect():
    """Заявка по договорам/Directum из общих вопросов направляется в 05."""
    task = {
        "Id": 140413,
        "ServiceId": 16,
        "ServiceName": "Общие вопросы",
        "Name": "Согласование договора",
        "Description": "Прошу проверить карточку договора 6/123 в директум и прикрепить скан контрагента.",
    }

    target_root, reason = classify_target_service(f"{task['Name']}. {task['Description']}", task["ServiceId"])
    assert target_root == "05"

    rule = ServiceRedirectRule()
    decision = rule.evaluate(task)

    assert decision is not None
    assert decision.is_redirect is True
    assert decision.target_root == "05"
    assert "05. Вопросы по DIRECTUM, B2B" in decision.name


@pytest.mark.asyncio
async def test_downtime_blocks_cancellation_in_rule_engine():
    """RuleEngine гарантирует, что любая заявка с маркерами простоя не закрывается со статусом 30."""
    task = {
        "Id": 140414,
        "ServiceId": 16,  # Общие вопросы
        "ServiceName": "Общие вопросы",
        "Name": "Авария на автовесовой",
        "Description": "Срочно, авария! Зависла программа, не можем взвешивать машины, происходит простой производства!",
    }

    engine = RuleEngine()
    decision, trace = engine.evaluate_with_trace(task)

    assert decision.status_id == 27
    assert decision.status_name == "В работе"
    assert decision.risk_level == "critical"
    assert decision.risk_warning is not None
    assert any(item["status"] == "matched" for item in trace)
