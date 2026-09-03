"""
Реэкспорт из единого пакета shared.json_utils для обратной совместимости (SSOT).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.json_utils import *  # noqa: F401, F403
