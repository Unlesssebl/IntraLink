from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def parse_api_date(date_str: str) -> datetime | None:
    """
    Парсит дату из API IntraService.
    Поддерживает форматы: YYYY-MM-DD HH:MM:SS, YYYY-MM-DDTHH:MM:SS, DD.MM.YYYY HH:MM:SS
    """
    if not date_str:
        return None

    date_str = date_str.replace("T", " ")

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    logger.warning("Не удалось спарсить дату: %s", date_str)
    return None
