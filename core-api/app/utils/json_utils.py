import logging
from typing import Any

try:
    import orjson

    def json_dumps(obj: Any, ensure_ascii: bool = False, indent: int | None = None) -> str:
        option = 0
        if indent:
            option |= orjson.OPT_INDENT_2
        return orjson.dumps(obj, default=str, option=option).decode('utf-8')

    def json_loads(data: str | bytes) -> Any:
        return orjson.loads(data)

except ImportError:
    import json

    logger = logging.getLogger(__name__)
    logger.warning('orjson не найден, используется стандартный модуль json')

    def json_dumps(obj: Any, ensure_ascii: bool = False, indent: int | None = None) -> str:
        return json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent, default=str)

    def json_loads(data: str | bytes) -> Any:
        return json.loads(data)
