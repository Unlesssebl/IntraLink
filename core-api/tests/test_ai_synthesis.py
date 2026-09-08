"""
Юнит-тесты для Фазы 5: AI-генерация ответов инженера (Response Synthesis)
и автоматическая канонизация базы знаний (Auto-KB Canonization).
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.ai.schemas import DataCircuit, RoutedInferenceResponse
from app.services.ai_synthesis import (
    _synthesize_deterministic_fallback,
    canonize_task_solution,
    synthesize_triage_resolution,
)
from app.services.rag import sync_historical_closed_tasks


# ===========================================================================
# 1. Тесты канонизации базы знаний (Auto-KB Canonization)
# ===========================================================================


def test_canonize_task_solution_noise_stripping_and_root_cause():
    """Проверка очистки от шума, подписей и определения первопричины сбоя."""
    task = {
        "Id": 12345,
        "Name": "Срочно помогите пожалуйста!",
        "Description": "Здравствуйте! Не печатает принтер Kyocera M2040dn, ошибка 0x0000011b.",
    }
    lifetime = [
        {"Comment": "Статус изменен на 'В работе'"},
        {"Comment": "Назначен исполнитель: Беликов Ален"},
        {
            "Comment": (
                "Удалил проблемное обновление KB5005565 и прописал ключ реестра RpcAuthnLevelExemption=0. "
                "Служба Spooler перезапущена. С уважением, инженер Беликов Ален, тел. 12-34"
            )
        },
    ]

    canon = canonize_task_solution(task, lifetime)

    assert "Здравствуйте" not in canon["problem"]
    assert "пожалуйста" not in canon["problem"]
    assert "Kyocera M2040dn" in canon["problem"]
    assert "0x0000011b" in canon["problem"]

    # Решение очищено от телефонной подписи
    assert "KB5005565" in canon["solution"]
    assert "RpcAuthnLevelExemption" in canon["solution"]
    assert "С уважением" not in canon["solution"]
    assert "тел. 12-34" not in canon["solution"]

    # Первопричина
    assert "Spooler" in canon["root_cause"] or "печати" in canon["root_cause"]
    assert "Первопричина:" in canon["canonical_summary"]


# ===========================================================================
# 2. Тесты детерминированного синтеза ответа (Deterministic Fallback)
# ===========================================================================


def test_synthesize_deterministic_fallback_printing():
    """Проверка формирования ответа по печати с учетом телеметрии хоста."""
    task = {
        "Id": 9988,
        "Name": "Не печатает принтер",
        "Description": "Зависла очередь печати",
    }
    telemetry = {
        "pc_name": "NTEMW0144",
        "ping_status": "ONLINE",
        "metrics": {"spooler": "RUNNING", "disk_free_gb": 45.0},
    }

    resp = _synthesize_deterministic_fallback(task=task, telemetry=telemetry)
    assert "Здравствуйте!" in resp
    assert "#9988" in resp
    assert "NTEMW0144" in resp
    assert "службы печати" in resp
    assert "диагностик" in resp.lower()
    assert "выполнен" not in resp.lower()


def test_synthesize_deterministic_fallback_rag_match():
    """Проверка формирования ответа на основе RAG прецедента."""
    task = {
        "Id": 7766,
        "Name": "Ошибка 1С",
        "Description": "Ошибка блокировки сеанса",
    }
    kb_matches = [
        {
            "solution": "Очищен локальный кэш пользователя в %LOCALAPPDATA%\\1C и завершен зависший сеанс."
        }
    ]

    resp = _synthesize_deterministic_fallback(task=task, kb_matches=kb_matches)
    assert "Здравствуйте!" in resp
    assert "#7766" in resp
    assert "Очищен локальный кэш" in resp
    assert "проверю его применимость" in resp


# ===========================================================================
# 3. Тесты Response Synthesis с контурами DLP Zero Trust
# ===========================================================================


@pytest.mark.asyncio
async def test_synthesize_triage_resolution_red_circuit_isolation():
    """Проверка, что для RED контура (пароли/секреты) облачный AI Hub не вызывается."""
    task = {
        "Id": 5544,
        "Name": "Сброс пароля",
        "Description": "Пользователь забыл пароль: SecretPassword123",
        "ServiceId": 42,
    }

    with patch.object(
        __import__("app.services.ai_synthesis", fromlist=["ai_hub"]).ai_hub,
        "dispatch_routed_inference",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        resp = await synthesize_triage_resolution(
            task=task,
            circuit=DataCircuit.RED,
        )

        assert "Здравствуйте!" in resp
        assert "#5544" in resp
        # RED контур: dispatch_routed_inference НЕ вызывался
        mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_triage_resolution_green_ai_hub():
    """Проверка генерации ответа через AI Hub с вызовом dispatch_routed_inference."""
    task = {
        "Id": 3322,
        "Name": "Установка шрифтов",
        "Description": "Требуется установить корпоративные шрифты",
        "ServiceId": 44,
    }

    mock_resp = RoutedInferenceResponse(
        circuit=DataCircuit.GREEN,
        model=settings.GEMINI_MODEL,
        text="Здравствуйте! Шрифты успешно установлены. Проверьте отображение.",
        sanitized_entities_count=0,
        cached=False,
        execution_time_ms=150.0,
    )

    with patch.object(
        __import__("app.services.ai_synthesis", fromlist=["ai_hub"]).ai_hub,
        "dispatch_routed_inference",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ) as mock_dispatch:
        resp = await synthesize_triage_resolution(
            task=task,
            circuit=DataCircuit.GREEN,
        )

        assert "Шрифты успешно установлены" in resp
        mock_dispatch.assert_called_once()


# ===========================================================================
# 4. Тест синхронизации закрытых заявок с канонизацией
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_historical_closed_tasks_canonization():
    """Проверка интеграции канонизации при выгрузке закрытых заявок в RAG."""
    mock_db = AsyncMock(spec=AsyncSession)

    tasks_mock = [
        {
            "Id": 8801,
            "Name": "Срочно помогите!",
            "Description": "Не работает печать на HP LaserJet",
            "StatusId": 29,
            "StatusName": "Выполнена",
            "ServiceId": 44,
            "ServiceName": "03. Печать",
        }
    ]

    lifetime_mock = [
        {"Comment": "Статус изменен"},
        {"Comment": "Служба Spooler перезапущена, пробная страница напечатана."},
    ]

    with patch(
        "app.services.intraservice.get_tasks",
        new_callable=AsyncMock,
        return_value=tasks_mock,
    ), patch(
        "app.services.intraservice.get_task_lifetime",
        new_callable=AsyncMock,
        return_value=lifetime_mock,
    ), patch(
        "app.services.rag.index_task_knowledge",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_index:
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_res

        sync_res = await sync_historical_closed_tasks(
            auth_b64="bW9ja19hdXRo",
            db=mock_db,
            days=30,
            limit=5,
        )

        assert sync_res["indexed"] == 1
        mock_index.assert_called_once()

        call_kwargs = mock_index.call_args[1]
        assert call_kwargs["task_id"] == 8801
        assert "HP LaserJet" in call_kwargs["problem"]
        assert "Срочно помогите" not in call_kwargs["problem"]
        assert "Spooler" in call_kwargs["solution"]
        assert "root_cause" in call_kwargs["classification_data"]


# ===========================================================================
# 5. Тесты Quality Gate (is_informative_solution) и привязки к регламенту
# ===========================================================================


def test_is_informative_solution_quality_gate():
    """Проверка фильтра качества: отсечение неинформативных закрытых заявок."""
    from app.services.ai_synthesis import is_informative_solution

    # Пустые и шаблонные отписки не должны проходить Quality Gate
    assert is_informative_solution("") is False
    assert is_informative_solution("ок") is False
    assert is_informative_solution("выполнено") is False
    assert is_informative_solution("готово") is False
    assert is_informative_solution("Заявка выполнена в штатном режиме.") is False
    assert is_informative_solution("заявка выполнена в штатном режиме") is False
    assert is_informative_solution("все работает спасибо") is False
    assert is_informative_solution("короткий текст") is False

    # Содержательные технические решения должны успешно проходить
    assert is_informative_solution(
        "Выполнена замена жесткого диска на SSD Kingston 480GB и установка Windows 10."
    ) is True
    assert is_informative_solution(
        "Очистили кэш пользователя в папке AppData/1C, информационная база успешно запущена."
    ) is True


def test_synthesize_deterministic_fallback_with_rule_decision():
    """Проверка того, что регламент из Rule Engine имеет наивысший приоритет."""
    task = {"Id": 140251, "Name": "Сломался ПК", "Description": "Не включается"}
    rule_decision = {
        "rule_type": "hardware_repair",
        "comment": "Приносите системный блок в АБК 3, 112 каб. на диагностику и обслуживание.",
        "status_id": 48,
    }

    resp = _synthesize_deterministic_fallback(
        task=task,
        rule_decision=rule_decision,
    )
    assert resp == "Приносите системный блок в АБК 3, 112 каб. на диагностику и обслуживание."


def test_physical_delivery_rule_semantic_anchors():
    """Проверка срабатывания PhysicalDeliveryRule по семантическим прототипам."""
    from app.services.rules.physical_device import PhysicalDeliveryRule

    rule = PhysicalDeliveryRule()
    # Текст без явных регулярок "диагностика пк" или "замена hdd", но с семантикой поломки оборудования
    task = {
        "Id": 140251,
        "Name": "Разбит экран ноутбука",
        "Description": "Уронили ноутбук со стола, треснул корпус и не загорается экран",
    }
    decision = rule.evaluate(task)
    assert decision is not None
    assert decision.status_id == 48
    assert "112" in decision.comment
    assert decision.template_key in ("hardware_repair", "bring_device_112")


def test_canonize_task_solution_ignores_applicant_remarks():
    """Проверка изоляции ролей: комментарий заявителя в конце тикета не должен становиться решением."""
    task = {
        "Id": 138928,
        "Name": "Создание почтового ящика",
        "Description": "Требуется почта",
        "CreatorId": 6064,
        "ExecutorIds": "8974",
        "StatusId": 29,
        "StatusName": "Закрыта",
    }
    lifetime = [
        {
            "Date": "2026-09-03T16:00:00",
            "EditorId": 8974,
            "Editor": "Набиев Искандер",
            "StatusId": 29,
            "Comments": "Почтовый ящик успешно создан в Exchange, реквизиты высланы руководителю.",
        },
        {
            "Date": "2026-09-03T16:05:00",  # Более свежий комментарий, но от заявителя!
            "EditorId": 6064,
            "Editor": "Абнагимов Роман",
            "StatusId": None,
            "Comments": "Спасибо большое, все проверил, пароль подошел!",
        },
    ]
    canon = canonize_task_solution(task, lifetime)
    assert "Exchange" in canon["solution"]
    assert "Спасибо большое" not in canon["solution"]
    assert canon["resolution_type"] == "resolved"
    assert canon["resolution_label"] == "Успешно выполнено"


def test_canonize_task_solution_ignores_system_auto_close():
    """Проверка игнорирования системных авто-закрытий и служебных логов IntraService."""
    from app.services.ai_synthesis import is_informative_solution
    task = {
        "Id": 133445,
        "Name": "Проблема с доступом",
        "CreatorId": 5555,
        "StatusId": 30,
        "StatusName": "Отменена",
    }
    lifetime = [
        {
            "Date": "2026-09-03T12:00:00",
            "EditorId": 0,
            "Editor": "IntraService",
            "StatusId": 30,
            "Comments": "Заявка автоматически переведена в статус 'Отменена' по истечении 72 часов 0 минут",
        },
    ]
    canon = canonize_task_solution(task, lifetime)
    assert canon["solution"] == ""
    assert is_informative_solution(canon["solution"]) is False


def test_classify_task_resolution_outcomes():
    """Проверка классификации исходов: resolved, rejected, redirected, duplicate, consultation."""
    from app.services.ai_synthesis import classify_task_resolution

    # 1. Resolved
    t1 = {"StatusId": 29, "StatusName": "Закрыта"}
    r1 = classify_task_resolution(t1, "Создали доменную учетную запись и добавили в группу 1С")
    assert r1["resolution_type"] == "resolved"

    # 2. Rejected
    t2 = {"StatusId": 30, "StatusName": "Отменена"}
    r2 = classify_task_resolution(t2, "Служебная записка не согласована службой безопасности, в доступе отказ")
    assert r2["resolution_type"] == "rejected"
    assert r2["resolution_badge_color"] == "rose"

    # 3. Duplicate
    t3 = {"StatusId": 30, "StatusName": "Отменена"}
    r3 = classify_task_resolution(t3, "Дубликат заявки #139800, работы ведутся в основной задаче")
    assert r3["resolution_type"] == "duplicate"
    assert r3["resolution_badge_color"] == "amber"

    # 4. Redirected
    t4 = {"StatusId": 30, "StatusName": "Отменена"}
    r4 = classify_task_resolution(t4, "Ошибочный сервис. Пожалуйста, создайте заявку в разделе '1С: Предприятие'")
    assert r4["resolution_type"] == "redirected"
    assert r4["resolution_badge_color"] == "sky"

    # 5. Clarification: вопрос инженера в статусе 35 ("Требует уточнения") не классифицируется как отказ
    t5 = {"StatusId": 35, "StatusName": "Требует уточнения"}
    r5 = classify_task_resolution(
        t5,
        "Добрый день, за указанным в текущей заявке ПК уже работает Ярулин. Данный сотрудник был уволен, переведен или будет работать совместно? Прошу дать ответ в комментариях.",
        status_id=35,
        status_name="Требует уточнения",
    )
    assert r5["resolution_type"] == "clarification"
    assert r5["resolution_label"] == "Требует уточнения"

    # 6. Незавершенная заявка в canonize_task_solution не возвращает решение (не индексируется как прецедент)
    task_in_progress = {"Id": 140194, "StatusId": 35, "StatusName": "Требует уточнения"}
    canon_in_progress = canonize_task_solution(
        task_in_progress,
        [{"Comment": "Данный сотрудник был уволен?", "EditorId": 10502, "StatusId": 35}]
    )
    assert "Данный сотрудник был уволен?" in canon_in_progress["solution"]
    assert canon_in_progress["resolution_type"] == "clarification"


def test_evaluate_solution_quality_fast_comprehensive():
    """Тест эвристической фильтрации отписок (Уровень 1)."""
    from app.services.ai_synthesis import evaluate_solution_quality_fast

    # Отписки должны отсекаться
    assert evaluate_solution_quality_fast("Не смог до вас дозвониться. Перезвоните по номеру 47-10.", 30)[0] is False
    assert evaluate_solution_quality_fast("Заявка отменена как повторная (дубликат инцидента #138597).", 30)[0] is False
    assert evaluate_solution_quality_fast("Акт прикрепил к заявке", 28)[0] is False
    assert evaluate_solution_quality_fast("Заявка не актуальна", 29)[0] is False
    assert evaluate_solution_quality_fast("Оставьте заявку в правильном разделе 08.", 30)[0] is False

    # Короткие для статуса 30 (<60 симв) должны отсекаться
    assert evaluate_solution_quality_fast("Отменено по согласованию сторон.", 30)[0] is False

    # Полезные инструкции должны проходить
    ok_valid, _ = evaluate_solution_quality_fast(
        "Выполнена замена жесткого диска на SSD Kingston 480GB и установка Windows 10.", 29
    )
    assert ok_valid is True

    # Защита от False Positive: длинная инструкция с тех. шагами не отсекается, даже если есть слово 'перезвоните'
    tech_instruction = (
        "1. Проверьте сетевой кабель. 2. Перезагрузите компьютер и проверьте IP-адрес через ipconfig. "
        "3. Служба печати Spooler была перезапущена на сервере. Если не дозвонитесь до дежурного, "
        "проверьте статус очереди локально."
    )
    ok_fp, _ = evaluate_solution_quality_fast(tech_instruction, 28)
    assert ok_fp is True


@pytest.mark.asyncio
async def test_evaluate_solution_quality_llm_logic():
    """Тест локальной AI-валидации качества (Уровень 2)."""
    from app.services.ai_synthesis import evaluate_solution_quality_llm
    from unittest.mock import AsyncMock, patch, MagicMock

    # Длинное решение (>150 симв) сразу проходит без вызова LLM
    long_text = "А" * 160
    assert await evaluate_solution_quality_llm("Проблема", long_text) is True

    # Для короткого решения вызывается LLM
    mock_res_pos = MagicMock()
    mock_res_pos.text = "1"

    with patch("app.services.ai.hub.ai_hub.dispatch_routed_inference", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = mock_res_pos
        res = await evaluate_solution_quality_llm("Не работает мышь", "Заменили батарейки в мыши.")
        assert res is True

    mock_res_neg = MagicMock()
    mock_res_neg.text = "0"

    with patch("app.services.ai.hub.ai_hub.dispatch_routed_inference", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = mock_res_neg
        res = await evaluate_solution_quality_llm("Не работает мышь", "Перезвоните в техподдержку.")
        assert res is False

@pytest.mark.asyncio
async def test_deep_audit_solution_with_llm_logic():
    """Тест глубокого ночного аудита решений через LLM без срезок длины."""
    from app.services.ai_synthesis import deep_audit_solution_with_llm
    from unittest.mock import AsyncMock, patch, MagicMock

    # 1. Пустое / сверхкороткое решение (<15 симв) бракуется сразу
    r_empty = await deep_audit_solution_with_llm("Проблема", "Ок")
    assert r_empty["score"] == 0.0
    assert r_empty["verdict"] == "blacklist"

    # 2. Очевидная отписка бракуется (score <= 0.2, verdict=blacklist)
    mock_res_dismissal = MagicMock()
    mock_res_dismissal.text = '{"score": 0.5, "verdict": "keep", "reason": "тест", "key_steps": []}'
    with patch("app.services.ai.hub.ai_hub.dispatch_routed_inference", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = mock_res_dismissal
        r_dism = await deep_audit_solution_with_llm("Интернет", "Не смог дозвониться до пользователя. Перезвоните по номеру 44-55.")
        assert r_dism["score"] <= 0.2
        assert r_dism["verdict"] == "blacklist"

    # 3. Качественное техническое решение одобряется с высоким скорингом
    mock_res_tech = MagicMock()
    mock_res_tech.text = '{"score": 0.85, "verdict": "keep", "reason": "Подробная инструкция", "key_steps": ["Очистить spooler", "Перезапустить службу"]}'
    with patch("app.services.ai.hub.ai_hub.dispatch_routed_inference", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = mock_res_tech
        r_tech = await deep_audit_solution_with_llm("Принтер", "Очистили C:\\Windows\\System32\\spool\\PRINTERS и перезапустили Spooler.")
        assert r_tech["score"] >= 0.7
        assert r_tech["verdict"] == "keep"
        assert len(r_tech["key_steps"]) == 2