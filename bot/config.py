import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN") or ""
CORE_API_URL: str = os.getenv("CORE_API_URL", "http://127.0.0.1:8000/api/v1")
BOT_API_KEY: str = os.getenv("BOT_API_KEY", "test_api_key_12345")
INTRAService_URL: str = os.getenv("INTRAService_URL", "https://servicedesk.corporate.loc/api/")
POLLING_INTERVAL: int = int(os.getenv("POLLING_INTERVAL", 10))
REDIS_URL: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")
if not CORE_API_URL:
    raise ValueError("CORE_API_URL is not set in environment variables")
if not BOT_API_KEY:
    raise ValueError("BOT_API_KEY is not set in environment variables")
if not INTRAService_URL:
    raise ValueError("INTRAService_URL is not set in environment variables")
if not REDIS_URL:
    raise ValueError("REDIS_URL is not set in environment variables")


