"""
Фоновый сервис исполнения задач в среде Windows (Active Directory, WinRM, DNS/SMB)
через персистентную очередь Redis Streams с поддержкой Command Bus, Event Hub (SSE) и HITL.
Включает Heartbeat-мониторинг доступности и Fail-Fast TCP Probing.
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any
import redis.asyncio as aioredis

from core_api_client import CoreApiClient
from diagnostics import run_host_diagnostics
from executors.ad import ActiveDirectoryExecutor
from executors.printers import PrinterExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [WIN-WORKER] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("execution_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_EXECUTION_QUEUE = "stream:execution_queue"
STREAM_GROUP_NAME = "execution_group"
CONSUMER_NAME = f"windows_node_{os.getenv('COMPUTERNAME', 'host')}"


class WindowsExecutionWorker:
    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self.redis: aioredis.Redis | None = None
        self.ad_exec = ActiveDirectoryExecutor()
        self.printer_exec = PrinterExecutor()
        self.api_client = CoreApiClient()
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None

    async def _heartbeat_loop(self) -> None:
        """Регулярный Heartbeat в Redis для отображения статуса воркера в Web SPA / Admin."""
        while self._running:
            try:
                if self.redis:
                    await self.redis.set("worker:health:win_daemon", "online", ex=25)
            except Exception as e:
                logger.debug("Ошибка отправки heartbeat: %s", e)
            await asyncio.sleep(10.0)

    async def _precheck_host_tcp(
        self, host: str, port: int = 5985, timeout_sec: float = 1.5
    ) -> bool:
        """Fail-Fast асинхронный TCP-чек порта перед тяжелым вызовом WinRM/WMI."""
        if not host:
            return False
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout_sec
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def start(self) -> None:
        """Запуск фонового воркера обработки задач из Redis Streams."""
        self.redis = aioredis.from_url(
            self.redis_url, decode_responses=True, socket_timeout=10.0
        )
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Создание Consumer Group, если еще не создана
        try:
            await self.redis.xgroup_create(
                STREAM_EXECUTION_QUEUE,
                STREAM_GROUP_NAME,
                id="0",
                mkstream=True,
            )
            logger.info("Создана Consumer Group '%s'", STREAM_GROUP_NAME)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.debug("xgroup_create info: %s", e)

        print(
            f"🚀 [Windows Execution Worker] Запущен. Ожидание задач из Redis Streams ({STREAM_EXECUTION_QUEUE})..."
        )
        print(f"   • Consumer: {CONSUMER_NAME} | Node: {os.name} (Windows)")

        self._concurrency_sem = asyncio.Semaphore(4)

        while self._running:
            try:
                # Чтение задач для Consumer Group
                events = await self.redis.xreadgroup(
                    groupname=STREAM_GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_EXECUTION_QUEUE: ">"},
                    count=5,
                    block=3000,
                )

                if not events:
                    continue

                for stream_name, messages in events:
                    for msg_id, data in messages:
                        asyncio.create_task(self._process_job_safe(msg_id, data))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Ошибка в цикле execution_worker: %s", e)
                await asyncio.sleep(2.0)

    async def _process_job_safe(self, msg_id: str, data: dict[str, Any]) -> None:
        """Безопасная конкурентная обработка задачи с подтверждением доставки XACK."""
        async with self._concurrency_sem:
            try:
                await self._process_job(msg_id, data)
            except Exception as e:
                logger.error("Критический сбой обработки задачи msg_id=%s: %s", msg_id, e)
            finally:
                if self.redis:
                    try:
                        await self.redis.xack(
                            STREAM_EXECUTION_QUEUE, STREAM_GROUP_NAME, msg_id
                        )
                    except Exception as e:
                        logger.debug("Ошибка XACK для %s: %s", msg_id, e)

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self.redis:
            await self.redis.delete("worker:health:win_daemon")
            await self.redis.close()
        await self.api_client.close()
        print("🛑 [Windows Execution Worker] Остановлен.")

    async def _publish_event(
        self, job_id: str, event_type: str, data: dict[str, Any]
    ) -> None:
        """Публикует событие в Redis Pub/Sub канал задачи и общий канал."""
        if not self.redis:
            return
        try:
            payload = {
                "event": event_type,
                "job_id": job_id,
                "timestamp": time.time(),
                "data": data,
            }
            raw = json.dumps(payload, ensure_ascii=False)
            await self.redis.publish(f"job:{job_id}:events", raw)
            await self.redis.publish("events:all", raw)
        except Exception as e:
            logger.debug(
                "Ошибка публикации события %s для %s: %s", event_type, job_id, e
            )

    async def _wait_for_confirmation(
        self,
        job_id: str,
        prompt: str,
        details: dict[str, Any],
        timeout_sec: float = 300.0,
    ) -> bool:
        """
        Режим Human-in-the-Loop: приостанавливает выполнение задачи,
        публикует запрос подтверждения и ожидает ответа оператора из Redis list.
        """
        if not self.redis:
            return False

        print(f"⚠️  [HITL Required] Задача {job_id}: {prompt}")
        await self._publish_event(
            job_id,
            "confirm_required",
            {"prompt": prompt, "details": details, "timeout_sec": timeout_sec},
        )

        # Обновляем статус задачи в Redis
        raw = await self.redis.get(f"execution_job:{job_id}")
        if raw:
            try:
                jdata = json.loads(raw)
                jdata["status"] = "confirm_required"
                jdata["confirm_prompt"] = prompt
                await self.redis.set(
                    f"execution_job:{job_id}",
                    json.dumps(jdata, ensure_ascii=False),
                    ex=3600 * 24 * 7,
                )
            except Exception:
                pass

        # Блокирующее ожидание ответа оператора
        start_t = time.time()
        confirm_key = f"job:{job_id}:confirm"
        while time.time() - start_t < timeout_sec:
            pop_res = await self.redis.brpop(confirm_key, timeout=5)
            if pop_res:
                _, val = pop_res
                try:
                    decision_obj = (
                        json.loads(val) if isinstance(val, str) else val
                    )
                    decision = (
                        decision_obj.get("decision", "")
                        if isinstance(decision_obj, dict)
                        else str(decision_obj)
                    )
                    if decision.lower() == "approve":
                        print(
                            f"✓ [HITL Approved] Оператор одобрил выполнение задачи {job_id}"
                        )
                        return True
                    else:
                        print(
                            f"❌ [HITL Rejected] Оператор отклонил выполнение задачи {job_id}"
                        )
                        return False
                except Exception as e:
                    logger.error("Ошибка парсинга решения HITL: %s", e)
                    return False

        print(
            f"⏰ [HITL Timeout] Истекло время ожидания подтверждения задачи {job_id}"
        )
        return False

    async def _process_job(self, msg_id: str, data: dict[str, Any]) -> None:
        job_id = data.get("job_id", "unknown")
        action = data.get("action", "")
        task_id_str = data.get("task_id", "0")
        task_id = int(task_id_str) if task_id_str.isdigit() else 0
        auto_close = data.get("auto_close", "true").lower() == "true"
        mode = data.get("mode", "auto").lower()

        raw_payload = data.get("payload", "{}")
        params = (
            json.loads(raw_payload)
            if isinstance(raw_payload, str)
            else raw_payload
        )

        print(
            f"\n⚡ [Job Received] ID: {job_id} | Действие: '{action}' | Тикет: #{task_id} | Режим: {mode}"
        )

        # Публикуем событие начала выполнения
        await self._publish_event(
            job_id,
            "started",
            {"action": action, "task_id": task_id, "node": CONSUMER_NAME},
        )

        result_status = "failed"
        result_message = ""
        result_payload = {}

        try:
            # 1. Выдача доступа Wi-Fi в AD (WLAN-WORKNET)
            if action in ("grant_wlan", "wifi"):
                await self._publish_event(
                    job_id,
                    "progress",
                    {
                        "phase": "searching_user",
                        "pct": 20,
                        "detail": "Определение пользователя",
                    },
                )
                identity = params.get("identity") or params.get("login")
                if not identity and task_id > 0:
                    task = await self.api_client.get_task_details(task_id)
                    if task:
                        identity = self.ad_exec.extract_identity_from_task(task)

                if not identity:
                    result_message = "Не удалось определить пользователя для выдачи доступа Wi-Fi."
                else:
                    if mode == "confirm":
                        approved = await self._wait_for_confirmation(
                            job_id,
                            prompt=f"Предоставить доступ Wi-Fi сотруднику '{identity}'?",
                            details={"identity": identity, "group": "WLAN-WORKNET"},
                        )
                        if not approved:
                            result_status = "rejected"
                            result_message = (
                                "Операция отклонена оператором Helpdesk."
                            )
                            return

                    await self._publish_event(
                        job_id,
                        "progress",
                        {
                            "phase": "ad_execution",
                            "pct": 60,
                            "detail": f"Добавление {identity} в группу WLAN-WORKNET",
                        },
                    )
                    res = await self.ad_exec.grant_wlan_access(identity)
                    result_status = "success" if res.success else "failed"
                    result_message = res.message
                    result_payload = {"log": res.log}

                    if res.success and task_id > 0 and auto_close:
                        await self._publish_event(
                            job_id,
                            "progress",
                            {
                                "phase": "closing_ticket",
                                "pct": 90,
                                "detail": "Закрытие заявки",
                            },
                        )
                        await self.api_client.add_comment(
                            task_id=task_id,
                            comment=f"Добрый день! Доступ к сети Wi-Fi успешно предоставлен для учетной записи {identity}.",
                            status_id=30,  # Выполнена/Закрыта
                            expenses=15,
                        )

            # 2. Установка / Диагностика принтера
            elif action in ("install_printer", "printer"):
                pc_name = params.get("pc_name") or params.get("target")
                printer_name = params.get("printer_name") or params.get(
                    "printer_address"
                )

                if not pc_name or not printer_name:
                    result_message = "Не указаны обязательные параметры (pc_name или printer_name)."
                else:
                    # Fail-Fast проверка доступности ПК по сети перед WinRM
                    await self._publish_event(
                        job_id,
                        "progress",
                        {
                            "phase": "host_ping",
                            "pct": 10,
                            "detail": f"Проверка доступности {pc_name}...",
                        },
                    )
                    is_online = await self._precheck_host_tcp(pc_name, 5985, 1.5)
                    if not is_online:
                        result_status = "failed"
                        result_message = f"Рабочая станция {pc_name} недоступна по сети (WinRM порт 5985 закрыт/выключен)."
                    else:
                        if mode == "confirm":
                            approved = await self._wait_for_confirmation(
                                job_id,
                                prompt=f"Установить принтер '{printer_name}' на ПК '{pc_name}'?",
                                details={
                                    "pc_name": pc_name,
                                    "printer_name": printer_name,
                                },
                            )
                            if not approved:
                                result_status = "rejected"
                                result_message = "Операция отклонена оператором."
                                return

                        await self._publish_event(
                            job_id,
                            "progress",
                            {
                                "phase": "installing",
                                "pct": 50,
                                "detail": f"Установка принтера {printer_name} на {pc_name}",
                            },
                        )
                        res = await self.printer_exec.install_printer(
                            pc_name, printer_name
                        )
                        result_status = "success" if res.success else "failed"
                        result_message = res.message
                        result_payload = {"log": res.log}

                        if res.success and task_id > 0 and auto_close:
                            await self.api_client.add_comment(
                                task_id=task_id,
                                comment=f"Добрый день! Принтер {printer_name} успешно подключен на вашем компьютере {pc_name}.",
                                status_id=30,
                                expenses=15,
                            )

            else:
                result_message = f"Неизвестное действие: '{action}'"

        except Exception as e:
            logger.exception("Исключение при выполнении задачи %s: %s", job_id, e)
            result_status = "failed"
            result_message = f"Внутренняя ошибка исполнения: {e}"

        finally:
            # Сохраняем финальный результат в Redis
            final_data = {
                "job_id": job_id,
                "status": result_status,
                "message": result_message,
                "payload": result_payload,
                "completed_at": time.time(),
                "node": CONSUMER_NAME,
            }
            if self.redis:
                await self.redis.set(
                    f"execution_job:{job_id}",
                    json.dumps(final_data, ensure_ascii=False),
                    ex=3600 * 24 * 7,
                )
                await self.redis.xack(
                    STREAM_EXECUTION_QUEUE, STREAM_GROUP_NAME, msg_id
                )

            # Публикуем событие завершения
            await self._publish_event(job_id, result_status, final_data)
            print(
                f"🏁 [Job Completed] ID: {job_id} | Статус: {result_status.upper()} | {result_message}\n"
            )


async def main():
    worker = WindowsExecutionWorker()
    try:
        await worker.start()
    except (KeyboardInterrupt, SystemExit):
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
