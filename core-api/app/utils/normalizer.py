"""
Реэкспорт из единого пакета shared.normalizer для обратной совместимости (SSOT).
"""
import sys
from pathlib import Path

# Гарантируем присутствие корня монорепозитория в sys.path
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.normalizer import *  # noqa: F401, F403
