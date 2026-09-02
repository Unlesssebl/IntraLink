"""
Фоновый сервис исполнения задач в среде Windows (Active Directory, WinRM, DNS/SMB)
через персистентную очередь Redis Streams с поддержкой Command Bus, Event Hub (SSE) и HITL.
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
        self.printer_exec = PrinterExecutor()
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
            logger.debug("Ошибка публикации события %s для %s: %s", event_type, job_id, e)

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
                    decision_obj = json.loads(val) if isinstance(val, str) else val
                    decision = (
                        decision_obj.get("decision", "")
                        if isinstance(decision_obj, dict)
                        else str(decision_obj)
                    )
                    if decision.lower() == "approve":
                        print(f"✓ [HITL Approved] Оператор одобрил выполнение задачи {job_id}")
                        return True
                    else:
                        print(f"❌ [HITL Rejected] Оператор отклонил выполнение задачи {job_id}")
                        return False
                except Exception as e:
                    logger.error("Ошибка парсинга решения HITL: %s", e)
                    return False

        print(f"⏰ [HITL Timeout] Истекло время ожидания подтверждения задачи {job_id}")
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
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )

        print(f"\n⚡ [Job Received] ID: {job_id} | Действие: '{action}' | Тикет: #{task_id} | Режим: {mode}")

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
                    {"phase": "searching_user", "pct": 20, "detail": "Определение пользователя"},
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
                            prompt=f"Выдать доступ к корпоративному Wi-Fi пользователю '{identity}'?",
                            details={"identity": identity, "group": "WLAN-WORKNET", "task_id": task_id},
                        )
                        if not approved:
                            result_status = "cancelled"
                            result_message = "Отменено оператором или превышен таймаут подтверждения."
                            raise Exception(result_message)

                    await self._publish_event(
                        job_id,
                        "progress",
                        {"phase": "granting_ad_group", "pct": 60, "detail": f"Добавление {identity} в группу AD"},
                    )
                    ad_res = await self.ad_exec.grant_wlan_access_async(identity)
                    if ad_res.success:
                        result_status = "success"
                        result_message = ad_res.message
                        if auto_close and task_id > 0:
                            await self._publish_event(
                                job_id,
                                "progress",
                                {"phase": "closing_ticket", "pct": 90, "detail": "Финализация заявки в IntraService"},
                            )
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
                await self._publish_event(
                    job_id,
                    "progress",
                    {"phase": "extracting_data", "pct": 20, "detail": "Извлечение анкеты нового сотрудника"},
                )
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
                    if mode == "confirm":
                        approved = await self._wait_for_confirmation(
                            job_id,
                            prompt=f"Создать учетную запись в Active Directory: {surname} {name} ({company or 'Организация'})?",
                            details={
                                "surname": surname,
                                "name": name,
                                "patronymic": patronymic,
                                "company": company,
                                "department": dept,
                                "task_id": task_id,
                            },
                        )
                        if not approved:
                            result_status = "cancelled"
                            result_message = "Создание пользователя отменено оператором."
                            raise Exception(result_message)

                    await self._publish_event(
                        job_id,
                        "progress",
                        {"phase": "creating_ad_account", "pct": 60, "detail": f"Создание SAM account в Active Directory"},
                    )
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
                            await self._publish_event(
                                job_id,
                                "progress",
                                {"phase": "closing_ticket", "pct": 90, "detail": "Сохранение реквизитов и закрытие заявки"},
                            )
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
                await self._publish_event(
                    job_id,
                    "progress",
                    {"phase": "pinging_host", "pct": 30, "detail": f"Тестирование доступности {target}"},
                )
                diag_res = await run_host_diagnostics(target)
                result_status = "success"
                result_payload = diag_res
                result_message = f"Хост {target}: {'Онлайн' if diag_res.get('is_online') else 'Офлайн'}"

            # 4. Установка / диагностика принтера
            elif action in ("install_printer", "printer"):
                pc_name = params.get("pc_name") or params.get("host") or ""
                printer_ip = params.get("printer_ip") or params.get("ip") or ""
                printer_model = params.get("printer_model") or params.get("model") or ""

                if mode == "confirm":
                    approved = await self._wait_for_confirmation(
                        job_id,
                        prompt=f"Установить принтер '{printer_model}' ({printer_ip}) на ПК '{pc_name}'?",
                        details={"pc_name": pc_name, "printer_ip": printer_ip, "model": printer_model, "task_id": task_id},
                    )
                    if not approved:
                        result_status = "cancelled"
                        result_message = "Установка принтера отменена оператором."
                        raise Exception(result_message)

                await self._publish_event(
                    job_id,
                    "progress",
                    {"phase": "installing_printer", "pct": 50, "detail": f"Установка драйвера и порта на {pc_name}"},
                )
                res = await self.printer_exec.install_printer_async(
                    pc_name=pc_name, printer_ip=printer_ip, printer_model=printer_model
                )
                result_status = "success" if res.success else "failed"
                result_message = res.message or res.error
                result_payload = {"success": res.success}

            else:
                result_message = f"Неизвестный тип действия: '{action}'"

        except Exception as e:
            if result_status != "cancelled":
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
                    "completed_at": time.time(),
                }
                await self.redis.set(
                    f"execution_job:{job_id}",
                    json.dumps(update_data, ensure_ascii=False),
                    ex=3600 * 24 * 7,
                )
                # Подтверждаем обработку сообщения в Stream
                await self.redis.xack(
                    STREAM_EXECUTION_QUEUE, STREAM_GROUP_NAME, msg_id
                )
                # Публикуем финальное событие result
                await self._publish_event(
                    job_id,
                    "result",
                    {
                        "status": result_status,
                        "message": result_message,
                        "data": result_payload,
                    },
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

