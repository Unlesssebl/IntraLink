"""
Юнит-тесты для сервиса фоновой экспресс-телеметрии хостов (Host Telemetry Service):
1. Fail-Fast каскад (ICMP 400ms, TCP Port 300ms, DNS).
2. Subnet Rate-Limiting (/24) и Host Concurrency Lock.
3. Сбор и парсинг CIM WinRM метрик (диск C:, Spooler, юзер).
4. Кэширование в Redis (diag:<task_id>) и Pre-fetch интеграция в Triage Hub.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import get_db
from app.main import app
from app.routers.deps import get_service_auth_b64
from app.services.host_telemetry import (
    collect_cim_metrics,
    collect_host_telemetry,
    extract_pc_from_task,
    fast_ping,
    format_telemetry_badge,
    get_subnet_from_ip,
    get_task_telemetry,
    prefetch_task_telemetry,
    probe_tcp_port,
    resolve_dns_fast,
    subnet_rate_limit,
)
from app.utils.json_utils import json_dumps

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest.fixture(autouse=True)
def override_deps():
    async def mock_get_service_auth_b64():
        return "bW9ja19hdXRoX2I2NA=="

    async def mock_get_db():
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


# ===========================================================================
# 1. Тесты утилит и извлечения имени ПК
# ===========================================================================


def test_get_subnet_from_ip():
    """Проверка извлечения подсети /24."""
    assert get_subnet_from_ip("10.244.1.45") == "10.244.1.0/24"
    assert get_subnet_from_ip("192.168.0.100") == "192.168.0.0/24"
    assert get_subnet_from_ip("invalid-ip") is None
    assert get_subnet_from_ip(None) is None


def test_extract_pc_from_task():
    """Проверка извлечения имени ПК из различных секций заявки."""
    # 1. Из _field_meta
    task1 = {"_field_meta": {"pc_name": "ntemw0144"}}
    assert extract_pc_from_task(task1) == "NTEMW0144"

    # 2. Из CustomFields (список)
    task2 = {"CustomFields": [{"FieldId": 1112, "Value": "ЗТЕ1234"}]}
    assert extract_pc_from_task(task2) == "ZTE1234"

    # 3. Из CustomFields (словарь)
    task3 = {"CustomFields": {"1112": "KMK0050"}}
    assert extract_pc_from_task(task3) == "KMK0050"

    # 4. Из темы / описания
    task4 = {
        "Name": "Ошибка принтера",
        "Description": "Не печатает на компьютере TLK1002 в кабинете 204",
    }
    assert extract_pc_from_task(task4) == "TLK1002"

    # 5. Если ПК не указан
    task5 = {"Name": "Общий вопрос", "Description": "Консультация"}
    assert extract_pc_from_task(task5) is None


# ===========================================================================
# 2. Тесты сетевого каскада (DNS, Ping, TCP Port)
# ===========================================================================


@pytest.mark.asyncio
async def test_resolve_dns_fast():
    """Проверка быстрого резолвинга DNS."""
    # Прямой IP
    assert await resolve_dns_fast("10.244.1.1") == "10.244.1.1"

    with patch("socket.gethostbyname", return_value="10.244.1.50"):
        ip = await resolve_dns_fast("NTEMW0144")
        assert ip == "10.244.1.50"


@pytest.mark.asyncio
async def test_fast_ping_online():
    """Проверка успешного ICMP пинга хоста."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(
            b"Reply from 10.244.1.50: bytes=32 time=2ms TTL=128\r\n",
            b"",
        )
    )

    with patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        res = await fast_ping("10.244.1.50", timeout_sec=0.4)
        assert res["is_online"] is True
        assert res["avg_rtt"] == "2ms"


@pytest.mark.asyncio
async def test_fast_ping_timeout():
    """Проверка отсечения оффлайн-хоста по таймауту 400 мс."""
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=TimeoutError("Ping timeout"),
    ):
        res = await fast_ping("10.244.1.99", timeout_sec=0.4)
        assert res["is_online"] is False
        assert res["avg_rtt"] is None


@pytest.mark.asyncio
async def test_probe_tcp_port():
    """Проверка доступности TCP-портов."""
    # Успешное подключение
    mock_writer = AsyncMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection",
        new_callable=AsyncMock,
        return_value=(AsyncMock(), mock_writer),
    ):
        assert await probe_tcp_port("10.244.1.50", 5985, timeout_sec=0.3) is True

    # Недоступный порт
    with patch(
        "asyncio.open_connection",
        side_effect=ConnectionRefusedError("Port closed"),
    ):
        assert (
            await probe_tcp_port("10.244.1.50", 5985, timeout_sec=0.3) is False
        )


# ===========================================================================
# 3. Тесты Subnet Rate-Limiter и CIM WinRM сбора
# ===========================================================================


@pytest.mark.asyncio
async def test_subnet_rate_limit():
    """Проверка троттлинга подсети."""
    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.decr = AsyncMock(return_value=0)

    async with subnet_rate_limit(
        "10.244.1.0/24", max_concurrent=3, redis_client=mock_redis
    ):
        pass

    mock_redis.incr.assert_called_once_with("ratelimit:subnet:10.244.1.0/24")
    mock_redis.decr.assert_called_once_with("ratelimit:subnet:10.244.1.0/24")


@pytest.mark.asyncio
async def test_collect_cim_metrics():
    """Проверка парсинга метрик CIM WinRM."""
    mock_raw_output = {
        "success": True,
        "data": {
            "disk_total": 137438953472,  # 128 GB
            "disk_free": 48318382080,  # 45 GB
            "spooler": "Running",
            "onec": "Running",
            "user": "TEMPO\\ivanov.ii",
        },
    }

    with patch(
        "app.services.host_telemetry.run_cim_winrm_metrics_sync",
        return_value=mock_raw_output,
    ):
        res = await collect_cim_metrics("NTEMW0144", timeout_sec=5)
        assert res["ok"] is True
        assert res["disk_c"]["total_gb"] == 128.0
        assert res["disk_c"]["free_gb"] == 45.0
        assert res["services"]["Spooler"] == "Running"
        assert res["services"]["1C:Enterprise"] == "Running"
        assert res["logged_in_user"] == "ivanov.ii"


# ===========================================================================
# 4. Тесты главного пайплайна collect_host_telemetry
# ===========================================================================


@pytest.mark.asyncio
async def test_collect_host_telemetry_host_not_specified():
    """Проверка возврата статуса HOST_NOT_SPECIFIED при отсутствии имени ПК."""
    mock_redis = AsyncMock()
    res = await collect_host_telemetry(
        pc_name=None, task_id=141001, redis_client=mock_redis
    )
    assert res["status"] == "HOST_NOT_SPECIFIED"
    assert res["pc_name"] is None
    assert "[Хост: ⚪ Не указан в заявке]" in res["badge"]


@pytest.mark.asyncio
async def test_collect_host_telemetry_offline():
    """Проверка Fail-Fast отсечения оффлайн-хоста без запуска WMI."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)

    with patch(
        "app.services.host_telemetry.resolve_dns_fast",
        new_callable=AsyncMock,
        return_value="10.244.1.99",
    ), patch(
        "app.services.host_telemetry.fast_ping",
        new_callable=AsyncMock,
        return_value={"is_online": False, "avg_rtt": None},
    ), patch(
        "app.services.host_telemetry.probe_tcp_port",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "app.services.host_telemetry.collect_cim_metrics",
        new_callable=AsyncMock,
    ) as mock_cim:
        res = await collect_host_telemetry(
            pc_name="ntemw0144", task_id=141002, redis_client=mock_redis
        )
        assert res["status"] == "OFFLINE"
        assert res["canonical_name"] == "NTEMW0144"
        assert res["resolved_ip"] == "10.244.1.99"
        assert "🔴 Оффлайн" in res["badge"]
        # WMI не должен вызываться для оффлайн машины
        mock_cim.assert_not_called()


@pytest.mark.asyncio
async def test_collect_host_telemetry_online_with_wmi():
    """Проверка полного сбора телеметрии для онлайн-хоста с WinRM."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)

    cim_metrics = {
        "ok": True,
        "disk_c": {"total_gb": 120.0, "free_gb": 45.0, "free_percent": 37.5},
        "services": {"Spooler": "Running"},
        "logged_in_user": "ivanov.ii",
    }

    with patch(
        "app.services.host_telemetry.resolve_dns_fast",
        new_callable=AsyncMock,
        return_value="10.244.1.50",
    ), patch(
        "app.services.host_telemetry.fast_ping",
        new_callable=AsyncMock,
        return_value={"is_online": True, "avg_rtt": "2ms"},
    ), patch(
        "app.services.host_telemetry.probe_tcp_port",
        new_callable=AsyncMock,
        side_effect=[True, True],  # 445 SMB, 5985 WinRM
    ), patch(
        "app.services.host_telemetry.collect_cim_metrics",
        new_callable=AsyncMock,
        return_value=cim_metrics,
    ), patch(
        "app.services.host_telemetry.host_concurrency_lock"
    ) as mock_lock:
        mock_lock.return_value.__aenter__.return_value = {"pc_name": "NTEMW0144"}
        mock_lock.return_value.__aexit__.return_value = None

        res = await collect_host_telemetry(
            pc_name="ntemw0144", task_id=141003, redis_client=mock_redis
        )
        assert res["status"] == "ONLINE"
        assert res["canonical_name"] == "NTEMW0144"
        assert res["disk_c"]["free_gb"] == 45.0
        assert res["services"]["Spooler"] == "Running"
        assert res["logged_in_user"] == "ivanov.ii"
        assert "🟢 Ping 2ms" in res["badge"]
        assert "💾 C: 45.0GB" in res["badge"]
        assert "🖨️ Spooler: OK" in res["badge"]
        assert "👤 ivanov.ii" in res["badge"]


@pytest.mark.asyncio
async def test_collect_host_telemetry_reads_redis_cache():
    """Проверка мгновенного чтения из Redis при повторном открытии заявки."""
    cached_payload = {
        "task_id": 141004,
        "canonical_name": "ZTE1234",
        "status": "ONLINE",
        "badge": "[ZTE1234: 🟢 Ping 1ms]",
        "cached": False,
    }
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json_dumps(cached_payload))

    res = await collect_host_telemetry(
        pc_name="zte1234",
        task_id=141004,
        use_cache=True,
        redis_client=mock_redis,
    )
    assert res["status"] == "ONLINE"
    assert res["cached"] is True
    assert res["canonical_name"] == "ZTE1234"


@pytest.mark.asyncio
async def test_get_and_prefetch_task_telemetry():
    """Проверка функций prefetch_task_telemetry и get_task_telemetry."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(
        return_value=json_dumps({"status": "ONLINE", "pc_name": "KMK0010"})
    )

    # get_task_telemetry
    cached = await get_task_telemetry(141005, redis_client=mock_redis)
    assert cached is not None
    assert cached["status"] == "ONLINE"
    assert cached["cached"] is True

    # prefetch_task_telemetry
    with patch(
        "app.services.host_telemetry.collect_host_telemetry",
        new_callable=AsyncMock,
        return_value={"status": "ONLINE", "task_id": 141006},
    ) as mock_collect:
        pre_res = await prefetch_task_telemetry(
            {"Id": 141006, "Name": "Тест", "_field_meta": {"pc_name": "KMK0010"}},
            redis_client=mock_redis,
        )
        assert pre_res["task_id"] == 141006
        mock_collect.assert_called_once()


# ===========================================================================
# 5. Интеграционные тесты с Triage Hub эндпоинтами
# ===========================================================================


@pytest.mark.asyncio
async def test_triage_batch_endpoint_includes_telemetry():
    """Эндпоинт /api/v1/triage/batch возвращает поле telemetry для каждой заявки."""
    mock_tasks = [
        {
            "Id": 141010,
            "Name": "Настройка почты на ПК: NTEMW0144",
            "Created": "2026-09-01T12:00:00",
            "StatusId": 26,
            "StatusName": "Новая",
            "ServiceId": 42,
            "ServiceName": "01. Учетные записи",
            "Creator": "Иванов И.И.",
            "CreatorPhone": "49-87",
            "_field_meta": {"pc_name": "NTEMW0144"},
        }
    ]

    telemetry_mock = {
        "status": "ONLINE",
        "canonical_name": "NTEMW0144",
        "badge": "[NTEMW0144: 🟢 Ping 2ms]",
    }

    with patch(
        "app.services.intraservice.get_tasks_by_filter",
        new_callable=AsyncMock,
        return_value=mock_tasks,
    ), patch(
        "app.routers.triage.get_skipped_task_ids",
        new_callable=AsyncMock,
        return_value=set(),
    ), patch(
        "app.routers.triage.search_knowledge_base",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.routers.triage.get_task_telemetry",
        new_callable=AsyncMock,
        return_value=telemetry_mock,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/triage/batch?limit=5", headers=HEADERS
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["tasks"]) == 1
            task_item = data["tasks"][0]
            assert "telemetry" in task_item
            assert task_item["telemetry"]["status"] == "ONLINE"
            assert task_item["telemetry"]["canonical_name"] == "NTEMW0144"


@pytest.mark.asyncio
async def test_triage_task_card_endpoint_includes_telemetry():
    """Эндпоинт /api/v1/triage/tasks/{task_id} возвращает телеметрию хоста."""
    task_mock = {
        "Id": 141011,
        "Name": "Проблема с печатью",
        "Description": "Принтер завис на компьютере ZTE1234",
        "ServiceId": 44,
        "ServiceName": "03. Печать",
    }
    telemetry_mock = {
        "status": "ONLINE",
        "canonical_name": "ZTE1234",
        "badge": "[ZTE1234: 🟢 Ping 1ms | 🖨️ Spooler: OK]",
    }

    with patch(
        "app.services.intraservice.get_single_task",
        new_callable=AsyncMock,
        return_value=task_mock,
    ), patch(
        "app.services.intraservice.get_task_lifetime",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.routers.triage.search_knowledge_base",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.routers.triage.get_task_telemetry",
        new_callable=AsyncMock,
        return_value=telemetry_mock,
    ), patch(
        "app.routers.triage.build_suggestion_state",
        new_callable=AsyncMock,
        return_value={"status": "idle"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/triage/tasks/141011", headers=HEADERS
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "telemetry" in data
            assert data["telemetry"]["status"] == "ONLINE"
            assert "🖨️ Spooler: OK" in data["telemetry"]["badge"]
