import os
import sys

# Добавляем пути к воркерам в sys.path для возможности импорта их внутренних модулей
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Функция для временного добавления пути в sys.path
def add_to_path(folder_name):
    path = os.path.join(PROJECT_ROOT, folder_name)
    if path not in sys.path:
        sys.path.insert(0, path)
