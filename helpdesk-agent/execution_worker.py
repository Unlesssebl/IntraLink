"""
Фоновый сервис исполнения задач в среде Windows (Active Directory, WinRM, DNS/SMB)
через персистентную очередь Redis Streams.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any
import redis.asyncio as aioredis

from core_api_client import CoreApiClient
from diagnostics import run_host_diagnostics
from executors.ad import ActiveDirectoryExecutor

logger = logging.getLogger("helpdesk_agent.execution_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_EXECUTION_QUEUE = "stream:execution_queue"
STREAM_GROUP_NAME = "execution_group"
CONSUMER_NAME = f"windows_node_{os.getenv('COMPUTERNAME', 'host')}"


class WindowsExecutionWorker:
    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self.redis: aioredis.Redis | None = None
        self.ad_exec = ActiveDirectoryExecutor()
        self.api_client = CoreApiClient()
        self._running = False

    async def start(self) -> None:
        """Запуск фонового воркера обработки задач из Redis Streams."""
        self.redis = aioredis.from_url(
            self.redis_url, decode_responses=True, socket_timeout=10.0
        )
        self._running = True

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
                        await self._process_job(msg_id, data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Ошибка в цикле execution_worker: %s", e)
                await asyncio.sleep(2.0)

    async def stop(self) -> None:
        self._running = False
        if self.redis:
            await self.redis.close()
        await self.api_client.close()
        print("🛑 [Windows Execution Worker] Остановлен.")

    async def _process_job(self, msg_id: str, data: dict[str, Any]) -> None:
        job_id = data.get("job_id", "unknown")
        action = data.get("action", "")
        task_id_str = data.get("task_id", "0")
        task_id = int(task_id_str) if task_id_str.isdigit() else 0
        auto_close = data.get("auto_close", "true").lower() == "true"

        raw_payload = data.get("payload", "{}")
        params = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )

        print(f"\n⚡ [Job Received] ID: {job_id} | Действие: '{action}' | Тикет: #{task_id}")

        result_status = "failed"
        result_message = ""
        result_payload = {}

        try:
            # 1. Выдача доступа Wi-Fi в AD (WLAN-WORKNET)
            if action in ("grant_wlan", "wifi"):
                identity = params.get("identity") or params.get("login")
                if not identity and task_id > 0:
                    task = await self.api_client.get_task_details(task_id)
                    if task:
                        identity = self.ad_exec.extract_identity_from_task(task)

                if not identity:
                    result_message = "Не удалось определить пользователя для выдачи доступа Wi-Fi."
                else:
                    ad_res = await self.ad_exec.grant_wlan_access_async(identity)
                    if ad_res.success:
                        result_status = "success"
                        result_message = ad_res.message
                        if auto_close and task_id > 0:
                            comm = (
                                "Доступ к Wi-Fi предоставлен.\n"
                                "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.\n"
                                "Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."
                            )
                            await self.api_client.apply_decision(
                                [task_id], status_id=29, comment=comm, expenses=10
                            )
                    else:
                        result_message = ad_res.message

            # 2. Создание пользователя в AD
            elif action in ("create_user", "new_user"):
                surname = params.get("surname")
                name = params.get("name")
                patronymic = params.get("patronymic")
                company = params.get("company")
                dept = params.get("department")
                phone = params.get("phone")
                pc_name = params.get("pc_name")
                title = params.get("title")

                if (not surname or not name) and task_id > 0:
                    task = await self.api_client.get_task_details(task_id)
                    if task:
                        det = self.ad_exec.extract_user_creation_details_from_task(task)
                        surname = surname or det.get("surname")
                        name = name or det.get("name")
                        patronymic = patronymic or det.get("patronymic")
                        company = company or det.get("company")
                        dept = dept or det.get("department")
                        phone = phone or det.get("phone")
                        pc_name = pc_name or det.get("pc_name")
                        title = title or det.get("title")

                if not surname or not name:
                    result_message = "Не указаны обязательные реквизиты (Фамилия, Имя)."
                else:
                    res = await self.ad_exec.create_user_account_async(
                        surname=surname,
                        name=name,
                        patronymic=patronymic,
                        company=company,
                        department=dept,
                        phone=phone,
                        pc_name=pc_name,
                        title=title,
                    )
                    if res.success:
                        result_status = "success"
                        result_message = res.message
                        result_payload = {
                            "login": res.sam_account_name,
                            "password": res.password,
                        }
                        if auto_close and task_id > 0:
                            comm = (
                                "Учетная запись успешно создана.\n"
                                f"Логин: {res.sam_account_name}\n"
                                f"Временный пароль: {res.password}\n"
                                "При первом входе в систему потребуется изменить пароль на постоянный."
                            )
                            await self.api_client.apply_decision(
                                [task_id], status_id=29, comment=comm, expenses=10
                            )
                    else:
                        result_message = res.error

            # 3. Сетевая диагностика
            elif action in ("diagnose_host", "diag"):
                target = params.get("host") or params.get("target") or ""
                diag_res = await run_host_diagnostics(target)
                result_status = "success"
                result_payload = diag_res
                result_message = f"Хост {target}: {'Онлайн' if diag_res.get('is_online') else 'Офлайн'}"

            else:
                result_message = f"Неизвестный тип действия: '{action}'"

        except Exception as e:
            logger.exception("Исключение при обработке задачи %s: %s", job_id, e)
            result_message = f"Исключение: {e}"

        # Обновляем состояние задачи в Redis
        if self.redis:
            try:
                update_data = {
                    "job_id": job_id,
                    "action": action,
                    "task_id": task_id,
                    "status": result_status,
                    "message": result_message,
                    "result": result_payload,
                }
                await self.redis.set(
                    f"execution_job:{job_id}",
                    json.dumps(update_data, ensure_ascii=False),
                    ex=3600 * 24,
                )
                # Подтверждаем обработку сообщения в Stream
                await self.redis.xack(
                    STREAM_EXECUTION_QUEUE, STREAM_GROUP_NAME, msg_id
                )
                print(f"✓ [Job Completed] {job_id} -> {result_status.upper()}: {result_message}")
            except Exception as e:
                logger.error("Ошибка сохранения статуса задачи %s: %s", job_id, e)


async def main():
    worker = WindowsExecutionWorker()
    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
