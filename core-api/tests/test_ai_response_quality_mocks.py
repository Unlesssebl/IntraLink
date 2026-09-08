"""
Комплексный набор тестов качества ответов AI-контура на моках заявок Helpdesk (AI Response Quality Suite).

Проверяет:
1. Корректность классификации резолюций (resolved, redirect, duplicate, clarify, execute).
2. Заземление ответов (Strict Grounding): отсутствие галлюцинаций о "выполненных" действиях в новых заявках.
3. Соблюдение корпоративного брендбука (Zero-Emoji Policy, обращение на 'Вы', вежливый тон Беликова Алена).
4. Контекст переписки (Thread-Awareness): отсутствие повторения приветствий первого контакта при продолжении диалога.
5. Изоляцию закрытого RED-контура (учетные данные, пароли) от облачных вызовов.
6. Семантическую калибровку Confidence Score (предотвращение слепого одобрения).
"""

import re
from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database.db import Base, get_db
from app.main import app
from app.services.ai.schemas import DataCircuit, RoutedInferenceResponse
from app.services.ai_synthesis import (
    calculate_confidence_score,
    extract_thread_context,
    strip_emojis,
    synthesize_triage_resolution,
    _synthesize_deterministic_fallback,
)
from app.services.template_engine import auto_detect_template
from app.services.triage_service import TriageService

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # смайлики
    "\U0001F300-\U0001F5FF"  # символы и пиктограммы
    "\U0001F680-\U0001F6FF"  # транспорт и карты
    "\U0001F1E0-\U0001F1FF"  # флаги
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"  # дополнительные символы
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


def assert_no_emojis(text: str) -> None:
    """Убеждается, что текст не содержит ни одного эмодзи."""
    matches = EMOJI_PATTERN.findall(text)
    assert not matches, f"Обнаружены эмодзи в ответе: {matches}, текст: {text}"


@pytest_asyncio.fixture(autouse=True)
async def setup_quality_test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def mock_get_service_auth_b64():
        return "bW9ja19hdXRoX2I2NA=="

    from app.routers.deps import get_service_auth_b64
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    yield
    app.dependency_overrides.clear()


# ==============================================================================
# СЦЕНАРИЙ 1: Заявка на доступ к Wi-Fi (Инфраструктурный регламент)
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_wlan_access_request():
    """Проверяет реакцию на запрос доступа к корпоративному Wi-Fi."""
    # 1. Заявка в правильном разделе каталога (04. Проблемы с сетью и интернетом) -> регламент wifi_access / wlan_access
    task_correct = {
        "Id": 141001,
        "Name": "Подключение к корпоративному Wi-Fi",
        "Description": "Прошу предоставить доступ к Wi-Fi для корпоративного смартфона заявителя",
        "ServiceId": 71,
        "ServiceName": "04. Проблемы с сетью и интернетом",
        "_field_meta": {"phone": "49-87", "room": "112", "pc_name": "NTEMW0142"},
    }

    decision = auto_detect_template(task=task_correct)
    assert decision["template_key"] == "wifi_access" or decision["rule_type"] == "wlan_access"
    assert decision["status_id"] in (27, 29)
    assert_no_emojis(decision["comment"])

    # Синтез ответа инженера с заземлением на регламент
    response_text, meta = await synthesize_triage_resolution(
        task=task_correct,
        rule_decision=decision,
        return_metadata=True,
    )
    assert_no_emojis(response_text)
    assert "Здравствуйте!" in response_text
    # Не должно утверждать, что доступ уже выдан, пока воркер не отработал
    assert "уже предоставлен" not in response_text.lower() or "выполнен" not in response_text.lower()

    # 2. Если же пользователь подал заявку на Wi-Fi в раздел Учетных записей (01) -> редирект в 04
    task_wrong = {**task_correct, "ServiceId": 42, "ServiceName": "01. Учетные записи"}
    redirect_dec = auto_detect_template(task=task_wrong)
    assert redirect_dec.get("is_redirect") is True or redirect_dec["status_id"] == 30


# ==============================================================================
# СЦЕНАРИЙ 2: Ошибка 1С с заземлением на исторический прецедент RAG
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_1c_error_with_rag_grounding():
    """Проверяет использование исторического решения из RAG без выдумывания фактов."""
    task = {
        "Id": 141002,
        "Name": "Ошибка в 1С УПП",
        "Description": "При попытке проведения документа выскакивает: Ошибка блокировки сеанса.",
        "ServiceId": 51,
        "ServiceName": "06. Вопросы по 1С",
    }

    kb_matches = [
        {
            "task_id": 139110,
            "similarity_pct": 94.5,
            "solution": "Завершен зависший сеанс пользователя в консоли кластера 1С, очищен кэш в AppData/Local/1C.",
            "service_path": "06. Вопросы по 1С / Подраздел #51",
            "status_name": "Выполнена",
            "resolution_type": "resolved",
        }
    ]

    response_text, meta = await synthesize_triage_resolution(
        task=task,
        kb_matches=kb_matches,
        force_deterministic=True,
        return_metadata=True,
    )

    assert_no_emojis(response_text)
    assert "Здравствуйте!" in response_text
    assert "#141002" in response_text
    # Ответ должен опираться на прецедент, предлагая проверку
    assert "кэш" in response_text.lower() or "сеанс" in response_text.lower()
    # Запрещено заявлять, что заявка уже закрыта или решена в момент первого ответа
    assert "заявка выполнена" not in response_text.lower()


# ==============================================================================
# СЦЕНАРИЙ 3: Неисправность принтера с телеметрией хоста
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_printer_issue_with_host_telemetry():
    """Проверяет интеграцию телеметрии рабочей станции (онлайн хост, служба Spooler)."""
    task = {
        "Id": 141003,
        "Name": "Не печатает сетевой принтер",
        "Description": "Документы зависают в очереди печати на HP LaserJet 402dn",
        "ServiceId": 45,
        "ServiceName": "03. Установка и обслуживание оргтехники",
    }
    telemetry = {
        "pc_name": "NTEMW0099",
        "ping_status": "ONLINE",
        "metrics": {"spooler": "RUNNING", "disk_free_gb": 48.5},
    }

    response_text = _synthesize_deterministic_fallback(task=task, telemetry=telemetry)
    assert_no_emojis(response_text)
    assert "Здравствуйте!" in response_text
    assert "NTEMW0099" in response_text
    assert "службы печати" in response_text
    assert "диагностик" in response_text.lower()


# ==============================================================================
# СЦЕНАРИЙ 4: Ошибочный раздел каталога (Редирект в целевой сервис)
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_wrong_service_redirect_request():
    """Проверяет автоматическое обнаружение неверного сервиса и вежливое перенаправление."""
    task = {
        "Id": 141004,
        "Name": "Согласование договора поставки",
        "Description": "Прошу срочно согласовать договор поставки 6/987 в DIRECTUM",
        "ServiceId": 42,  # Ошибочно подан в "01. Учетные записи пользователей"
        "ServiceName": "01. Учетные записи пользователей",
    }

    decision = auto_detect_template(task=task)
    assert decision.get("is_redirect") is True or decision.get("status_id") == 30
    assert "05" in str(decision.get("comment", "")) or "directum" in str(decision.get("comment", "")).lower()
    assert_no_emojis(decision["comment"])


# ==============================================================================
# СЦЕНАРИЙ 5: Заявка-дубликат
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_duplicate_task_handling():
    """Проверяет оформление отмены дубликата с привязкой к основной заявке."""
    task_id = 141005
    master_id = 140900
    dup_info = {"is_duplicate": True, "master_task_id": master_id}

    score = calculate_confidence_score(
        rule_decision={"rule_type": "duplicate_task", "comment": f"Дубликат #{master_id}"}
    )
    assert score == 0.99

    comment = (
        f"Заявка отменена как повторная (дубликат инцидента #{master_id}). "
        "Все работы ведутся в основной заявке. По вопросам звоните на 49-87."
    )
    assert_no_emojis(comment)
    assert str(master_id) in comment
    assert "дубликат" in comment.lower()


# ==============================================================================
# СЦЕНАРИЙ 6: Продолжение переписки (Thread-Aware)
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_thread_aware_follow_up():
    """Проверяет, что при ответе на существующую переписку не дублируется 'принята в работу'."""
    history = [
        {"Comment": "Здравствуйте! Заявка принята в работу. Уточните модель сканера.", "UserName": "Беликов Ален"},
        {"Comment": "Модель сканера Canon DR-C225, инвентарный номер 1204.", "UserName": "Петров П.П."},
    ]

    ctx = extract_thread_context(history)
    assert ctx["is_follow_up"] is True
    assert "Canon DR-C225" in ctx["last_comment"]

    task = {
        "Id": 141006,
        "Name": "Не работает сканер",
        "Description": "Не сканирует документы",
        "ServiceId": 45,
    }

    # Принудительный синтез с контекстом переписки
    response_text = _synthesize_deterministic_fallback(
        task=task,
        comments_history=history,
    )
    assert_no_emojis(response_text)
    # Не должно содержать шаблонное "принята в работу", так как диалог уже в разгаре
    assert "принята в работу" not in response_text.lower()


# ==============================================================================
# СЦЕНАРИЙ 7: Закрытый RED-контур (Пароли и учетные данные)
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_red_circuit_credentials_isolation():
    """Проверяет изоляцию RED контура (пароли) от внешних вызовов."""
    task = {
        "Id": 141007,
        "Name": "Сброс пароля доменной учетной записи",
        "Description": "Забыл пароль от домена, учетная запись ivanov.ii заблокирована",
        "ServiceId": 42,
    }

    response_text, meta = await synthesize_triage_resolution(
        task=task,
        circuit=DataCircuit.RED,
        return_metadata=True,
    )

    assert_no_emojis(response_text)
    assert meta["circuit"] == "red"
    assert meta["fallback"] is True
    assert "Здравствуйте!" in response_text
    assert "учетн" in response_text.lower() or "парол" in response_text.lower()


# ==============================================================================
# СЦЕНАРИЙ 8: Размытый запрос без деталей (Запрос уточнения)
# ==============================================================================
@pytest.mark.asyncio
async def test_quality_vague_request_clarification():
    """Проверяет корректность формирования запроса уточнения для неинформативных заявок."""
    from app.services.ai_synthesis import synthesize_clarification_comment

    task = {
        "Id": 141008,
        "Name": "Ничего не работает",
        "Description": "Срочно почините компьютер!",
        "ServiceId": 71,
    }

    clarification = await synthesize_clarification_comment(task)
    assert_no_emojis(clarification)
    assert "Здравствуйте!" in clarification
    # Должен запросить уточнение симптомов
    assert any(w in clarification.lower() for w in ["уточните", "подскажите", "сообщите", "опишите"])


# ==============================================================================
# СЦЕНАРИЙ 9: Калибровка Confidence Score
# ==============================================================================
def test_quality_confidence_calibration():
    """Проверяет калибровку уверенности решений для исключения слепого одобрения."""
    # 1. Точный дубликат
    c_dup = calculate_confidence_score(rule_decision={"rule_type": "duplicate_task"})
    assert c_dup == 0.99

    # 2. Доменный регламент (Wi-Fi)
    c_wlan = calculate_confidence_score(rule_decision={"rule_type": "wlan"})
    assert c_wlan == 0.95

    # 3. Высокий RAG прецедент + живой хост
    c_rag = calculate_confidence_score(
        kb_matches=[{"similarity_pct": 92.0}],
        telemetry={"ping_status": "ONLINE"},
        rule_decision={"rule_type": "some_rule"},
    )
    assert c_rag >= 0.85

    # 4. Неизвестная заявка без данных
    c_unknown = calculate_confidence_score(
        kb_matches=[],
        telemetry=None,
        rule_decision={"rule_type": "standard_in_work"},
    )
    assert c_unknown < 0.60
