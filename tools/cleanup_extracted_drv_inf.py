"""
Скрипт очистки extracted-drv-inf от мусорных папок:
- Без INF вообще
- Только с Autorun.inf (нет поля Class=)
- Без единого INF с Class=Printer

Запуск: python tools/cleanup_extracted_drv_inf.py [--dry-run]
"""
import sys
import os
import re

# Добавляем корень printer-worker в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'printer-worker')))

import asyncio
import smbclient
from worker_services.credentials import get_domain_credentials, format_smb_username

BASE_EXTRACTED = r'\\truenas\Drivers\printer\Hp\!HP-universal-drivers\extracted-drv-inf'

DRY_RUN = '--dry-run' in sys.argv


def has_printer_class_inf(folder_unc: str) -> bool:
    """Проверяет, есть ли в папке хотя бы один INF с Class=Printer (ищет рекурсивно)."""
    try:
        for root, dirs, files in smbclient.walk(folder_unc):
            for entry in files:
                if not entry.lower().endswith('.inf'):
                    continue
                full = root + '\\' + entry
                try:
                    with smbclient.open_file(full, mode='rb') as f:
                        raw = f.read()
                    import codecs
                    if raw.startswith(codecs.BOM_UTF16_LE):
                        content = raw.decode('utf-16-le')
                    elif raw.startswith(codecs.BOM_UTF8):
                        content = raw.decode('utf-8-sig')
                    else:
                        try:
                            # Пытаемся сначала как UTF-8, если есть невалидные байты — бросит ошибку
                            content = raw.decode('utf-8')
                        except UnicodeDecodeError:
                            # Старые драйвера (ANSI)
                            content = raw.decode('cp1251', errors='ignore')

                    for line in content.splitlines():
                        line = line.strip()
                        if not line or line.startswith(';'):
                            continue
                        if re.match(r'^class\s*=', line, re.IGNORECASE):
                            val = line.split('=', 1)[1].strip().strip('"').strip("'").lower()
                            if val == 'printer':
                                return True
                            break  # Class нашли, но не Printer — дальше не ищем в этом файле
                except Exception:
                    continue
    except Exception:
        pass
    return False


def rmdir_recursive_smb(path: str):
    """Рекурсивно удаляет папку на SMB-шаре."""
    try:
        for entry in smbclient.listdir(path):
            full = path + '\\' + entry
            try:
                stat = smbclient.stat(full)
                import stat as stat_module
                if stat_module.S_ISDIR(stat.st_mode):
                    rmdir_recursive_smb(full)
                else:
                    smbclient.remove(full)
            except Exception as e:
                print(f'  [WARN] Не удалось обработать {full}: {e}')
        smbclient.rmdir(path)
    except Exception as e:
        print(f'  [ERROR] Не удалось удалить {path}: {e}')


async def main():
    domain, username, password = await get_domain_credentials()
    full_username = format_smb_username('truenas', domain, username)
    smbclient.register_session('truenas', username=full_username, password=password)

    print(f'Сканирование {BASE_EXTRACTED}...')
    print(f'Режим: {"DRY RUN (удаления не будет)" if DRY_RUN else "РЕАЛЬНОЕ УДАЛЕНИЕ"}')
    print()

    to_delete = []
    kept = 0

    for entry in smbclient.listdir(BASE_EXTRACTED):
        folder = BASE_EXTRACTED + '\\' + entry
        try:
            s = smbclient.stat(folder)
            import stat as stat_module
            if not stat_module.S_ISDIR(s.st_mode):
                continue
        except Exception:
            continue

        if has_printer_class_inf(folder):
            print(f'  [OK]     {entry}')
            kept += 1
        else:
            print(f'  [МУСОР]  {entry}  → {"пропуск (dry-run)" if DRY_RUN else "УДАЛЕНИЕ"}')
            to_delete.append((entry, folder))

    print()
    print(f'Итого: {kept} нормальных, {len(to_delete)} мусорных')

    if to_delete and not DRY_RUN:
        confirm = input(f'\nУдалить {len(to_delete)} папок? (yes/no): ').strip().lower()
        if confirm == 'yes':
            for name, path in to_delete:
                print(f'  Удаление {name}...')
                rmdir_recursive_smb(path)
            print('Готово.')
        else:
            print('Отменено.')
    elif to_delete and DRY_RUN:
        print('\n[DRY RUN] Удаление не выполнено. Запустите без --dry-run для реального удаления.')


if __name__ == '__main__':
    asyncio.run(main())
