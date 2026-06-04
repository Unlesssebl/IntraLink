import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
INTRAService_URL = os.getenv("INTRAService_URL")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", 10))

# Путь к БД и настройки прокси
DB_PATH = os.getenv("DB_PATH", "intrabot.db")
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY")
INTRAService_PROXY = os.getenv("INTRAService_PROXY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")
if not INTRAService_URL:
    raise ValueError("INTRAService_URL is not set in environment variables")

