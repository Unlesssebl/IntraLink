"""
Реэкспорт из единого пакета shared.diagnostics для обратной совместимости.
"""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.diagnostics import *  # noqa: F401, F403
