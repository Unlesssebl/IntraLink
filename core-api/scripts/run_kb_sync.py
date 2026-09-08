"""
Скрипт автономного запуска умного наполнения базы знаний RAG.
Запускает стратифицированную синхронизацию по всем разделам каталога услуг IntraService
с заполнением иерархических путей, контролем качества и эмбеддингами.
"""

import asyncio
import base64
import logging
import sys

from app.database.db import AsyncSessionLocal
from app.services.crypto import decrypt_token
from app.services.rag import sync_stratified_kb
from app.services.vault import KEY_SERVICE_ACCOUNT, get_raw_setting

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("rag_sync_runner")


async def main() -> None:
    logger.info("Подготовка к запуску наполнения RAG...")

    async with AsyncSessionLocal() as db:
        service_cfg = await get_raw_setting(db, KEY_SERVICE_ACCOUNT)
        if not service_cfg or not service_cfg.get("login"):
            logger.error("Сервисный аккаунт не настроен в Vault (system_settings)!")
            sys.exit(1)

        pwd = decrypt_token(service_cfg["encrypted_password"])
        login = service_cfg["login"].strip()
        auth_b64 = base64.b64encode(f"{login}:{pwd}".encode("utf-8")).decode("ascii")

    logger.info("Сервисная учетная запись обнаружена: %s. Запуск sync_stratified_kb...", login)

    try:
        result = await sync_stratified_kb(
            auth_b64=auth_b64,
            quota_per_service=30,
            days=90,
            status_ids=[28, 29, 43, 30],
            ai_eval=True,
        )
        logger.info("Наполнение RAG успешно завершено: %s", result)
    except Exception as e:
        logger.exception("Ошибка при выполнении sync_stratified_kb: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
