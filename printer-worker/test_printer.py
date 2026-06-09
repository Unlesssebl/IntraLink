import asyncio
import logging
import sys
import os

# Добавляем пути, чтобы импорты работали
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("test_install")

from orchestrator.schemas import PrintJob, JobState, ConnectionType

from strategies import get_strategy
from executors.wmi_executor import WMIExecutor
import worker_config as config

async def test_install():
    target_pc = "itt0024"
    model_key = "kyocera_ecosys_m2040dn" # ключ из KB
    
    logger.info("Загрузка базы знаний...")
    import json
    with open(config.PRINTERS_KB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    from orchestrator.schemas import KnowledgeBase
    kb = KnowledgeBase.model_validate(data)
    
    driver_info = kb.find_by_key(model_key)
    if not driver_info:
        logger.error(f"Драйвер {model_key} не найден!")
        return
        
    logger.info(f"Найден драйвер: {driver_info.display_name}")
    
    # Формируем фиктивную задачу
    job = PrintJob(
        task_id=99999,
        tg_user_id=123,
        raw_text="Тестовая установка",
        state=JobState.PROBING,
        target_pc=target_pc,
        model_key=model_key,
        connection_type=ConnectionType.TCPIP,
        printer_address="ittp0000",
        driver_info=driver_info
    )
    
    domain = ""
    username = config.WINRM_USERNAME
    if "\\" in username:
        domain, username = username.split("\\", 1)

    wmi_exec = WMIExecutor(
        target_ip=job.target_pc,
        username=username,
        password=config.WINRM_PASSWORD,
        domain=domain
    )

    logger.info("Попытка включения WinRM через WMI...")
    try:
        await wmi_exec.enable_winrm()
        logger.info("WinRM успешно включен!")
    except Exception as e:
        logger.error(f"Ошибка включения WinRM: {e}")
        return

    try:
        strategy = get_strategy(job.connection_type)
        logger.info("Начало выполнения стратегии установки...")
        
        # probe
        job = await strategy.probe(job)
        if job.state == JobState.FAILED:
            logger.error(f"Диагностика не пройдена: {job.error_message}")
            return
            
        # execute
        job = await strategy.execute(job)
        if job.state == JobState.DONE:
            logger.info("Установка успешно завершена!")
        else:
            logger.error(f"Установка завершилась с ошибкой: {job.error_message}")
            
    finally:
        logger.info("Отключение WinRM...")
        try:
            await wmi_exec.disable_winrm()
            logger.info("WinRM успешно отключен!")
        except Exception as e:
            logger.error(f"Ошибка при отключении WinRM: {e}")

if __name__ == "__main__":
    asyncio.run(test_install())
