import os
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Основные параметры интеграции
CORE_API_URL: str = os.getenv("CORE_API_URL", "http://127.0.0.1:8000/api/v1")
BOT_API_KEY: str = os.getenv("BOT_API_KEY") or ""
REDIS_URL: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# Параметры авторизации WinRM на целевых ПК
WINRM_USERNAME: str = os.getenv("WINRM_USERNAME") or ""
WINRM_PASSWORD: str = os.getenv("WINRM_PASSWORD") or ""
WINRM_TRANSPORT: str = os.getenv("WINRM_TRANSPORT", "ntlm")

# Параметры интеграции с LLM
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")
LLM_API_URL: str = os.getenv("LLM_API_URL", "http://127.0.0.1:11434")
LLM_API_KEY: str = os.getenv("LLM_API_KEY") or ""
LLM_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", "llama3")

# Настройки базы знаний и печати
PRINTERS_KB_PATH: str = os.getenv("PRINTERS_KB_PATH", "knowledge_base/printers_knowledge_base.json")
PRINT_OUTPUT_DIR: str = os.getenv("PRINT_OUTPUT_DIR", "./prints")
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "5"))

# Время списания трудозатрат по умолчанию при успешной установке (в минутах)
WORKLOG_MINUTES: int = int(os.getenv("WORKLOG_MINUTES", "15"))

# ID аккаунта исполнителя в IntraService (ваш IS user ID).
# Если задан — воркер будет обрабатывать только заявки, где этот пользователь назначен исполнителем.
# Если не задан — фильтр по исполнителю не применяется (обрабатываются все принтерные заявки).
_executor_id_raw = os.getenv("PRINTER_EXECUTOR_IS_USER_ID", "")
PRINTER_EXECUTOR_IS_USER_ID: int | None = int(_executor_id_raw) if _executor_id_raw.strip().isdigit() else None

# Логин аккаунта исполнителя в IntraService (например, intratest).
# Используется как альтернатива PRINTER_EXECUTOR_IS_USER_ID для удобства.
PRINTER_EXECUTOR_LOGIN: str = os.getenv("PRINTER_EXECUTOR_LOGIN", "").strip()


# Валидация обязательных параметров
if not BOT_API_KEY:
    raise ValueError("BOT_API_KEY is not set in environment variables")
if not CORE_API_URL:
    raise ValueError("CORE_API_URL is not set in environment variables")
if not REDIS_URL:
    raise ValueError("REDIS_URL is not set in environment variables")
if not WINRM_USERNAME:
    raise ValueError("WINRM_USERNAME is not set in environment variables")
if not WINRM_PASSWORD:
    raise ValueError("WINRM_PASSWORD is not set in environment variables")
