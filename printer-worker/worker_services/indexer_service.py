import os
import json
import re
import tempfile
import subprocess
import logging
from datetime import datetime
import smbclient
from worker_services.credentials import get_domain_credentials, format_smb_username

logger = logging.getLogger(__name__)

# Папка с драйверами HP на сетевой шаре
HP_DRIVERS_SHARE = r"\\truenas\Drivers\printer\Hp"
INDEX_FILENAME = "hp_driver_index.json"

# Мусорные папки, которые следует пропускать при сканировании
SKIP_DIRS = {"installercontent", "help", "system32", "temp", "_temp", "assets", "xml", "scanner", "новая папка", "nueva carpeta"}

# Ключевые слова в именах папок/файлов, сигнализирующие о прошивке или полном ПО (не драйвер принтера)
SKIP_NON_DRIVER_KEYWORDS = {
    "firmware", "микропрограмм", "upgrade", "update",
    "full_software", "fullsoftware", "full software",
    "integratedinstaller", "integrated_installer",
}


# ---------------------------------------------------------------------------
# Вспомогательные фильтры и парсеры
# ---------------------------------------------------------------------------

def is_valid_inf_path(path: str) -> bool:
    path_lower = path.lower()
    invalid_keywords = ["xp", "vista", "win2000", "ia64", "itanium", "win9x", "nt4"]
    return not any(kw in path_lower for kw in invalid_keywords)


def get_arch_score(path: str) -> int:
    path_lower = path.lower()
    if "x64" in path_lower or "amd64" in path_lower or "64bit" in path_lower or "win64" in path_lower:
        return 10
    if "x86" in path_lower or "i386" in path_lower or "32bit" in path_lower or "win32" in path_lower:
        return 1
    return 5


def parse_inf_content(content: str) -> set:
    """Парсит текст INF-файла и возвращает множество имён моделей принтеров."""
    # Проверяем, что INF-файл действительно является драйвером принтера
    is_printer_class = False
    for line in content.splitlines():
        line_lower = line.strip().lower()
        if not line_lower or line_lower.startswith(';'):
            continue
        if line_lower.startswith('class') and '=' in line_lower:
            key = line_lower.split('=', 1)[0].strip()
            if key == 'class':
                val = line_lower.split('=', 1)[1].strip().strip('"').strip("'")
                if val == 'printer':
                    is_printer_class = True
                # Как только встретили точный Class, можно выходить из поиска
                break

    if not is_printer_class:
        return set()

    models = set()
    pattern = re.compile(r'^"([^"]+)"\s*=\s*[^,]+,')
    in_model_section = False

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section_name = line[1:-1].lower()
            in_model_section = section_name not in (
                "version", "manufacturer", "strings",
                "sourcedisksnames", "sourcedisksfiles", "destinationdirs"
            )
            continue

        if in_model_section:
            match = pattern.search(line)
            if match:
                model_name = match.group(1).strip()
                if len(model_name) > 3 and not model_name.startswith('%'):
                    models.add(model_name)
    return models


# ---------------------------------------------------------------------------
# Вывод имени серии из моделей INF
# ---------------------------------------------------------------------------

def _decode_inf_bytes(raw: bytes) -> str:
    """Декодирует байты INF-файла с автоопределением кодировки (BOM, utf-16le, utf-8, cp1251)."""
    import codecs
    if raw.startswith(codecs.BOM_UTF16_LE):
        return raw.decode('utf-16-le', errors='ignore')
    elif raw.startswith(codecs.BOM_UTF8):
        return raw.decode('utf-8-sig', errors='ignore')
    
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1251', errors='ignore')


def derive_series_name_from_models(models: set) -> str | None:
    """
    Выводит читаемое имя серии принтеров из набора моделей INF-файла.

    Примеры:
      {'HP LaserJet M253', 'HP LaserJet M254'} → 'HP_LaserJet_M253-M254'
      {'HP LaserJet 1018', 'HP LaserJet 1020', 'HP LaserJet 1022'} → 'HP_LaserJet_1018-1020-1022'
    """
    if not models:
        return None

    # Убираем незначимые суффиксы-варианты (PCL 6, PostScript, PS, буква-суффикс)
    variant_re = re.compile(
        r'\s+(?:PCL\s*\d*|PostScript|PS|XL|NW|DN|DW|TN|[NDWLX])$',
        re.IGNORECASE
    )
    base_names = sorted({variant_re.sub('', m).strip() for m in models if m.strip()})
    if not base_names:
        return None

    # Ищем общий словесный префикс
    words_list = [name.split() for name in base_names]
    common_words: list[str] = []
    for group in zip(*words_list):
        if len({w.upper() for w in group}) == 1:
            common_words.append(group[0])
        else:
            break

    prefix_str = ' '.join(common_words)

    # Собираем уникальные «хвосты» (отличия между моделями)
    diff_parts = sorted({
        name[len(prefix_str):].strip()
        for name in base_names
        if name[len(prefix_str):].strip()
    })

    if diff_parts:
        # До 4 вариантов — перечисляем через дефис; больше — диапазон first-last
        suffix = '-'.join(diff_parts) if len(diff_parts) <= 4 else f"{diff_parts[0]}-{diff_parts[-1]}"
        series = f"{prefix_str} {suffix}".strip()
    else:
        series = prefix_str

    if not series:
        return None

    # Нормализуем в имя папки: убираем недопустимые символы, пробелы → _
    series = re.sub(r'[^\w\s\-]', '', series)
    series = re.sub(r'\s+', '_', series)
    series = re.sub(r'[-_]{2,}', '_', series)
    return series.strip('_-') or None


def _get_series_from_smb_files(smb_root: str, files: list) -> str | None:
    """Читает INF-файлы из папки на сетевой шаре и выводит имя серии принтеров."""
    for fname in files:
        if not fname.lower().endswith('.inf') or not is_valid_inf_path(fname):
            continue
        try:
            with smbclient.open_file(smb_root + '\\' + fname, mode='rb') as f:
                raw = f.read()
            models = parse_inf_content(_decode_inf_bytes(raw))
            series = derive_series_name_from_models(models)
            if series:
                return series
        except Exception as e:
            logger.debug("Пропуск INF %s при определении серии: %s", fname, e)
    return None


def _get_series_from_local_dir(local_dir: str) -> str | None:
    """Читает INF-файлы из локальной папки (после распаковки) и выводит имя серии."""
    for local_root, _, files in os.walk(local_dir):
        for fname in files:
            if not fname.lower().endswith('.inf') or not is_valid_inf_path(fname):
                continue
            try:
                with open(os.path.join(local_root, fname), 'rb') as f:
                    raw = f.read()
                models = parse_inf_content(_decode_inf_bytes(raw))
                series = derive_series_name_from_models(models)
                if series:
                    return series
            except Exception as e:
                logger.debug("Пропуск INF %s при определении серии: %s", fname, e)
    return None


# ---------------------------------------------------------------------------
# Основная логика: сканирование, распаковка, копирование, индексация
# ---------------------------------------------------------------------------

async def rebuild_index_only() -> dict:
    """
    Быстрая переиндексация: читает только папку extracted-drv-inf и пересобирает индекс.
    Не сканирует и не обходит всю шару. Занимает несколько секунд.
    Используется когда администратор вручную добавил папку с драйвером в extracted-drv-inf.
    """
    logger.info("Запуск быстрой переиндексации из extracted-drv-inf...")
    domain, username, password = await get_domain_credentials()
    full_username = format_smb_username("truenas", domain, username)
    smbclient.register_session("truenas", username=full_username, password=password)

    stats = {"indexed": 0, "copied": 0, "extracted": 0, "skipped": 0}
    base_extracted_dir = HP_DRIVERS_SHARE + '\\!HP-universal-drivers\\extracted-drv-inf'
    models_dict = {}

    try:
        for root, dirs, files in smbclient.walk(base_extracted_dir):
            for file in files:
                if not file.lower().endswith('.inf'):
                    continue
                full_path = root + '\\' + file
                if not is_valid_inf_path(full_path):
                    continue
                try:
                    with smbclient.open_file(full_path, mode='rb') as f:
                        raw = f.read()
                    content = _decode_inf_bytes(raw)
                    found_models = parse_inf_content(content)
                    if found_models:
                        rel_path = full_path.replace(HP_DRIVERS_SHARE + '\\', '')
                        arch_score = get_arch_score(rel_path)
                        for model_name in found_models:
                            if model_name in models_dict:
                                if arch_score > models_dict[model_name]["arch_score"]:
                                    models_dict[model_name] = {
                                        "driver_name": model_name,
                                        "inf_path_suffix": rel_path,
                                        "arch_score": arch_score,
                                    }
                            else:
                                models_dict[model_name] = {
                                    "driver_name": model_name,
                                    "inf_path_suffix": rel_path,
                                    "arch_score": arch_score,
                                }
                except Exception as e:
                    logger.warning("Ошибка чтения %s: %s", full_path, e)

        final_models = {
            k: {
                "driver_name": v["driver_name"],
                "inf_path_suffix": v["inf_path_suffix"].replace('\\', '/'),
            }
            for k, v in models_dict.items()
        }
        index_data = {
            "version": "Aggregated UPD Index 2.0 (Centralized)",
            "base_dir": HP_DRIVERS_SHARE,
            "generated_at": datetime.now().isoformat(),
            "models": final_models,
        }
        index_smb_path = HP_DRIVERS_SHARE + '\\' + INDEX_FILENAME
        with smbclient.open_file(index_smb_path, mode='w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

        stats["indexed"] = len(final_models)
        logger.info("Быстрая переиндексация завершена! Найдено %d моделей.", len(final_models))
        await download_indexes_from_smb(session_registered=True)

    except Exception as e:
        logger.exception("Критическая ошибка во время быстрой переиндексации: %s", e)
        raise

    return stats


async def auto_extract_and_index():
    logger.info("Запуск процесса переиндексации драйверов и автораспаковки архивов...")
    domain, username, password = await get_domain_credentials()
    full_username = format_smb_username("truenas", domain, username)
    smbclient.register_session("truenas", username=full_username, password=password)

    # Счётчики статистики
    stats = {"indexed": 0, "copied": 0, "extracted": 0, "skipped": 0}

    invalid_cache_path = HP_DRIVERS_SHARE + '\\invalid_installers.json'
    invalid_cache = {}
    try:
        with smbclient.open_file(invalid_cache_path, mode='r', encoding='utf-8') as f:
            invalid_cache = json.load(f)
    except Exception:
        pass

    try:
        models_dict = {}
        directories_to_copy = []
        directories_to_extract = []

        base_extracted_dir = HP_DRIVERS_SHARE + '\\!HP-universal-drivers\\extracted-drv-inf'
        try:
            smbclient.makedirs(base_extracted_dir)
        except Exception:
            pass

        # ----------------------------------------------------------------
        # 1. Сканируем шару: собираем что копировать, а что распаковывать
        # ----------------------------------------------------------------
        logger.info("Сканирование директории: %s", HP_DRIVERS_SHARE)
        for root, dirs, files in smbclient.walk(HP_DRIVERS_SHARE):
            # Фильтруем dirs на месте (in-place), чтобы не заходить в мусорные папки
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and d.lower() not in ('!hp-universal-drivers', 'extracted-drv-inf')]

            # Проверяем части пути root на наличие мусорных папок
            root_parts = {p.lower() for p in root.split('\\')}
            if (root_parts & SKIP_DIRS) or '!hp-universal-drivers' in root_parts or 'extracted-drv-inf' in root_parts:
                dirs.clear()
                continue

            # Проверяем глубину вложенности относительно корня
            rel_path = root[len(HP_DRIVERS_SHARE):].strip('\\')
            depth = len(rel_path.split('\\')) if rel_path else 0

            # Ограничиваем общую глубину обхода (не глубже 3 уровней)
            if depth > 3:
                dirs.clear()
                continue

            if root == HP_DRIVERS_SHARE:
                continue

            logger.info("Обработка папки при сканировании: %s (depth=%d)", root, depth)
            has_inf = any(f.lower().endswith('.inf') for f in files)

            if has_inf:
                # Нашли папку с INF драйвером, больше в её подпапки не углубляемся
                dirs.clear()

                # Имя серии из содержимого INF с Class=Printer
                series_name = _get_series_from_smb_files(root, files)
                if not series_name:
                    logger.warning(
                        "Пропуск папки '%s': есть INF файлы, но ни в одном не найдено Class=Printer моделей.",
                        root.split('\\')[-1],
                    )
                    continue

                target_dir = base_extracted_dir + '\\' + series_name

                # Проверяем существование (дешёвый stat вместо listdir)
                try:
                    smbclient.stat(target_dir)
                    logger.debug("Уже обработано (stat): %s", target_dir)
                    continue
                except Exception:
                    pass

                directories_to_copy.append({
                    "root": root,
                    "target_dir": target_dir,
                    "files": files,
                    "series_name": series_name,
                })

            elif '_extracted' not in root.lower():
                # Нет INF — ищем архив для распаковки только на 1-2 уровнях глубины
                if depth <= 2:
                    # Пропускаем папки прошивок и полного ПО — они не содержат Class=Printer INF
                    folder_lower = root.split('\\')[-1].lower()
                    if any(kw in folder_lower for kw in SKIP_NON_DRIVER_KEYWORDS):
                        logger.info(
                            "Пропуск папки с архивом '%s': соответствует шаблону firmware/full-software",
                            root.split('\\')[-1],
                        )
                        dirs.clear()
                        continue

                    installers = [f for f in files if f.lower().endswith(('.exe', '.zip', '.cab'))]
                    if installers:
                        # Берём архив с самым коротким именем (обычно основной пакет)
                        target_installer = sorted(installers, key=lambda x: len(x))[0]
                        # Временное имя папки — определим после распаковки (из INF)
                        default_name = re.sub(r'[\s,]+', '_', root.split('\\')[-1]).strip('_')
                        directories_to_extract.append({
                            "root": root,
                            "installer": target_installer,
                            "default_name": default_name,
                            "depth": depth,
                        })

        # ----------------------------------------------------------------
        # 2. Копирование готовых папок с INF
        # ----------------------------------------------------------------
        for entry in directories_to_copy:
            root = entry["root"]
            target_dir = entry["target_dir"]
            logger.info("Копирование '%s' → '%s'", root.split('\\')[-1], entry["series_name"])
            try:
                smbclient.makedirs(target_dir)
            except Exception:
                pass

            for file in entry["files"]:
                src_path = root + '\\' + file
                dst_path = target_dir + '\\' + file
                try:
                    with smbclient.open_file(src_path, mode='rb') as s_file:
                        with smbclient.open_file(dst_path, mode='wb') as d_file:
                            d_file.write(s_file.read())
                except Exception as e:
                    logger.warning("Ошибка копирования %s: %s", src_path, e)
            stats["copied"] += 1

        # ----------------------------------------------------------------
        # 3. Авто-распаковка архивов
        # ----------------------------------------------------------------
        for entry in directories_to_extract:
            root = entry["root"]
            target_installer = entry["installer"]
            installer_smb_path = root + '\\' + target_installer

            rel_installer_path = installer_smb_path.replace(HP_DRIVERS_SHARE + '\\', '')

            # Проверка кэша невалидных архивов
            try:
                stat_info = smbclient.stat(installer_smb_path)
                mtime = stat_info.st_mtime
                if invalid_cache.get(rel_installer_path) == mtime:
                    logger.debug("Пропуск известного невалидного архива (из кэша): %s", rel_installer_path)
                    stats["skipped"] += 1
                    continue
            except Exception:
                pass

            logger.info("Попытка автораспаковки: %s", installer_smb_path)

            with tempfile.TemporaryDirectory() as temp_dir:
                local_installer_path = os.path.join(temp_dir, target_installer)
                local_extracted_dir = os.path.join(temp_dir, '_extracted')
                os.makedirs(local_extracted_dir, exist_ok=True)

                try:
                    logger.debug("Скачивание %s...", installer_smb_path)
                    with smbclient.open_file(installer_smb_path, mode='rb') as s_file:
                        with open(local_installer_path, 'wb') as d_file:
                            d_file.write(s_file.read())

                    logger.debug("Распаковка %s...", local_installer_path)
                    subprocess.run(
                        ['7z', 'x', f'-o{local_extracted_dir}', '-y', local_installer_path],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                    # Имя серии — из INF с Class=Printer, извлечённых локально
                    series_name = _get_series_from_local_dir(local_extracted_dir)
                    if not series_name:
                        logger.warning(
                            "Пропуск распакованного архива '%s': не найдено ни одного INF с Class=Printer. "
                            "Вероятно, это прошивка или пакет ПО без принт-драйвера.",
                            installer_smb_path,
                        )
                        # Добавляем в кэш невалидных, чтобы больше не распаковывать
                        try:
                            stat_info = smbclient.stat(installer_smb_path)
                            invalid_cache[rel_installer_path] = stat_info.st_mtime
                        except Exception:
                            pass
                        continue

                    target_dir = base_extracted_dir + '\\' + series_name

                    # Проверяем, не занято ли имя
                    try:
                        smbclient.stat(target_dir)
                        logger.debug("Уже обработано (stat): %s", target_dir)
                        continue
                    except Exception:
                        pass

                    logger.info("Распаковка '%s' → '%s'", target_installer, series_name)
                    stats["extracted"] += 1

                    try:
                        smbclient.makedirs(target_dir)
                    except Exception:
                        pass

                    for local_root, _, local_files in os.walk(local_extracted_dir):
                        for lf in local_files:
                            l_file_path = os.path.join(local_root, lf)
                            rel_dir = os.path.relpath(local_root, local_extracted_dir)

                            remote_dir = target_dir
                            if rel_dir != '.':
                                remote_dir = target_dir + '\\' + rel_dir.replace('/', '\\')
                                try:
                                    smbclient.makedirs(remote_dir)
                                except Exception:
                                    pass

                            remote_file_path = remote_dir + '\\' + lf
                            with open(l_file_path, 'rb') as s_file:
                                with smbclient.open_file(remote_file_path, mode='wb') as d_file:
                                    d_file.write(s_file.read())

                except Exception as e:
                    logger.error("Ошибка при распаковке %s: %s", installer_smb_path, e)
                    stats["skipped"] += 1

        # ----------------------------------------------------------------
        # 4. Построение индекса ТОЛЬКО по папке extracted-drv-inf
        # ----------------------------------------------------------------
        logger.info("Построение финального индекса из %s", base_extracted_dir)
        for root, dirs, files in smbclient.walk(base_extracted_dir):
            for file in files:
                if not file.lower().endswith('.inf'):
                    continue
                full_path = root + '\\' + file
                if not is_valid_inf_path(full_path):
                    continue

                try:
                    with smbclient.open_file(full_path, mode='rb') as f:
                        raw = f.read()
                    content = _decode_inf_bytes(raw)

                    found_models = parse_inf_content(content)
                    if found_models:
                        rel_path = full_path.replace(HP_DRIVERS_SHARE + '\\', '')
                        arch_score = get_arch_score(rel_path)

                        for model_name in found_models:
                            if model_name in models_dict:
                                if arch_score > models_dict[model_name]["arch_score"]:
                                    models_dict[model_name] = {
                                        "driver_name": model_name,
                                        "inf_path_suffix": rel_path,
                                        "arch_score": arch_score,
                                    }
                            else:
                                models_dict[model_name] = {
                                    "driver_name": model_name,
                                    "inf_path_suffix": rel_path,
                                    "arch_score": arch_score,
                                }
                except Exception as e:
                    logger.warning("Ошибка чтения %s: %s", full_path, e)

        # ----------------------------------------------------------------
        # 5. Сохранение финального индекса
        # ----------------------------------------------------------------
        final_models = {
            k: {
                "driver_name": v["driver_name"],
                "inf_path_suffix": v["inf_path_suffix"].replace('\\', '/'),
            }
            for k, v in models_dict.items()
        }

        index_data = {
            "version": "Aggregated UPD Index 2.0 (Centralized)",
            "base_dir": HP_DRIVERS_SHARE,
            "generated_at": datetime.now().isoformat(),
            "models": final_models,
        }

        index_smb_path = HP_DRIVERS_SHARE + '\\' + INDEX_FILENAME
        with smbclient.open_file(index_smb_path, mode='w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

        # Сохраняем обновленный кэш невалидных архивов
        try:
            with smbclient.open_file(invalid_cache_path, mode='w', encoding='utf-8') as f:
                json.dump(invalid_cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Не удалось сохранить кэш невалидных архивов: %s", e)

        logger.info("Индексация успешно завершена! Найдено %d моделей.", len(final_models))
        stats["indexed"] = len(final_models)

        # Скачиваем индекс локально для роутера (сессия уже зарегистрирована)
        await download_indexes_from_smb(session_registered=True)

    except Exception as e:
        logger.exception("Критическая ошибка во время индексации: %s", e)
        raise

    return stats


async def download_indexes_from_smb(session_registered: bool = False):
    """Скачивает готовые индексы драйверов с сетевой шары в локальную папку.

    Args:
        session_registered: если True — сессия уже зарегистрирована вызывающим кодом,
                            повторная регистрация не нужна.
    """
    logger.info("Синхронизация индексов драйверов с %s...", HP_DRIVERS_SHARE)
    try:
        if not session_registered:
            domain, username, password = await get_domain_credentials()
            full_username = format_smb_username("truenas", domain, username)
            smbclient.register_session("truenas", username=full_username, password=password)

        index_smb_path = HP_DRIVERS_SHARE + '\\' + INDEX_FILENAME
        local_kb_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
        os.makedirs(local_kb_dir, exist_ok=True)
        local_index_path = os.path.join(local_kb_dir, INDEX_FILENAME)

        try:
            with smbclient.open_file(index_smb_path, mode='rb') as s_file:
                with open(local_index_path, 'wb') as d_file:
                    d_file.write(s_file.read())
            logger.info("Индекс %s успешно загружен в локальный кэш.", INDEX_FILENAME)
        except Exception as e:
            logger.warning("Не удалось скачать %s с сервера: %s", index_smb_path, e)

    except Exception as e:
        logger.error("Ошибка при синхронизации индексов: %s", e)
