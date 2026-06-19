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

def is_valid_inf_path(path: str) -> bool:
    path_lower = path.lower()
    invalid_keywords = ["xp", "vista", "win2000", "ia64", "itanium", "win9x", "nt4"]
    for kw in invalid_keywords:
        if kw in path_lower:
            return False
    return True

def get_arch_score(path: str) -> int:
    path_lower = path.lower()
    if "x64" in path_lower or "amd64" in path_lower or "64bit" in path_lower or "win64" in path_lower:
        return 10
    if "x86" in path_lower or "i386" in path_lower or "32bit" in path_lower or "win32" in path_lower:
        return 1
    return 5

def parse_inf_content(content: str) -> set:
    models = set()
    pattern = re.compile(r'^"([^"]+)"\s*=\s*[^,]+,')
    in_model_section = False
    
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section_name = line[1:-1].lower()
            if section_name in ("version", "manufacturer", "strings", "sourcedisksnames", "sourcedisksfiles", "destinationdirs"):
                in_model_section = False
            else:
                in_model_section = True
            continue
            
        if in_model_section:
            match = pattern.search(line)
            if match:
                model_name = match.group(1).strip()
                if len(model_name) > 3 and not model_name.startswith('%'):
                    models.add(model_name)
    return models

async def auto_extract_and_index():
    logger.info("Запуск процесса переиндексации драйверов и автораспаковки архивов...")
    domain, username, password = await get_domain_credentials()
    full_username = format_smb_username("truenas", domain, username)
    smbclient.register_session("truenas", username=full_username, password=password)
    
    try:
        models_dict = {}
        directories_to_extract = []
        directories_to_copy = []
        
        base_extracted_dir = HP_DRIVERS_SHARE + '\\!HP-universal-drivers\\extracted-drv-inf'
        try:
            smbclient.makedirs(base_extracted_dir)
        except Exception:
            pass

        # 1. Сканируем всю шару, собираем что копировать, а что распаковывать
        logger.info(f"Сканирование директории: {HP_DRIVERS_SHARE}")
        for root, dirs, files in smbclient.walk(HP_DRIVERS_SHARE):
            # Пропускаем папку назначения, чтобы не было рекурсии
            if '!hp-universal-drivers' in root.lower() or 'extracted-drv-inf' in root.lower() or root == HP_DRIVERS_SHARE:
                continue
                
            has_inf = any(f.lower().endswith('.inf') for f in files)
            
            folder_name = root.split('\\')[-1]
            target_dir = base_extracted_dir + '\\' + folder_name
            
            # Проверяем, есть ли уже такая папка в целевой директории
            target_exists = False
            try:
                if len(smbclient.listdir(target_dir)) > 0:
                    target_exists = True
            except Exception:
                pass
                
            if target_exists:
                continue

            if has_inf:
                # Папка с готовыми INF, нужно скопировать
                directories_to_copy.append({
                    "root": root,
                    "target_dir": target_dir,
                    "files": files
                })
            elif '_extracted' not in root.lower():
                # Папка без INF, ищем архив
                installers = [f for f in files if f.lower().endswith(('.exe', '.zip', '.cab'))]
                if installers:
                    target_installer = sorted(installers, key=lambda x: len(x))[0] # Берем с самым коротким именем (или можно сортировать по размеру, если нужно)
                    directories_to_extract.append({
                        "root": root,
                        "installer": target_installer,
                        "target_dir": target_dir
                    })

        # 2. Копирование готовых папок
        for entry in directories_to_copy:
            root = entry["root"]
            target_dir = entry["target_dir"]
            logger.info("Копирование готовых драйверов из %s в %s", root, target_dir)
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

        # 3. Авто-распаковка архивов
        for entry in directories_to_extract:
            root = entry["root"]
            target_installer = entry["installer"]
            target_dir = entry["target_dir"]
            installer_smb_path = root + '\\' + target_installer
            
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
                    cmd = ['7z', 'x', f'-o{local_extracted_dir}', '-y', local_installer_path]
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
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

        # 4. Построение индекса ТОЛЬКО по папке extracted-drv-inf
        logger.info("Построение финального индекса из %s", base_extracted_dir)
        for root, dirs, files in smbclient.walk(base_extracted_dir):
            for file in files:
                if file.lower().endswith('.inf'):
                    full_path = root + '\\' + file
                    if not is_valid_inf_path(full_path):
                        continue
                        
                    try:
                        with smbclient.open_file(full_path, mode='r', encoding='utf-16le', errors='ignore') as f:
                            content = f.read()
                        if '\x00' not in content and 'Manufacturer' not in content:
                            with smbclient.open_file(full_path, mode='r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                                
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
                                            "arch_score": arch_score
                                        }
                                else:
                                    models_dict[model_name] = {
                                        "driver_name": model_name,
                                        "inf_path_suffix": rel_path,
                                        "arch_score": arch_score
                                    }
                    except Exception as e:
                        logger.warning("Ошибка чтения %s: %s", full_path, e)
                        
        # 5. Сохранение финального индекса
        final_models = {}
        for k, v in models_dict.items():
            final_models[k] = {
                "driver_name": v["driver_name"],
                "inf_path_suffix": v["inf_path_suffix"].replace('\\', '/')
            }
            
        index_data = {
            "version": "Aggregated UPD Index 2.0 (Centralized)",
            "base_dir": HP_DRIVERS_SHARE,
            "generated_at": datetime.now().isoformat(),
            "models": final_models
        }
        
        index_smb_path = HP_DRIVERS_SHARE + '\\' + INDEX_FILENAME
        with smbclient.open_file(index_smb_path, mode='w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
            
        logger.info("Индексация успешно завершена! Найдено %d моделей.", len(final_models))
        
        # Сразу скачиваем его локально для роутера
        await download_indexes_from_smb()
        
    except Exception as e:
        logger.exception("Критическая ошибка во время индексации: %s", e)
    finally:
        pass

async def download_indexes_from_smb():
    """Скачивает готовые индексы драйверов с сетевой шары в локальную папку."""
    logger.info("Синхронизация индексов драйверов с %s...", HP_DRIVERS_SHARE)
    try:
        domain, username, password = await get_domain_credentials()
        full_username = format_smb_username("truenas", domain, username)
        smbclient.register_session("truenas", username=full_username, password=password)
        
        index_smb_path = HP_DRIVERS_SHARE + '\\' + INDEX_FILENAME
        local_kb_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
        os.makedirs(local_kb_dir, exist_ok=True)
        local_index_path = os.path.join(local_kb_dir, INDEX_FILENAME)
        
        # Скачиваем hp_driver_index.json
        try:
            with smbclient.open_file(index_smb_path, mode='rb') as s_file:
                with open(local_index_path, 'wb') as d_file:
                    d_file.write(s_file.read())
            logger.info("Индекс %s успешно загружен в локальный кэш.", INDEX_FILENAME)
        except Exception as e:
            logger.warning("Не удалось скачать %s с сервера: %s", index_smb_path, e)
            
    except Exception as e:
        logger.error("Ошибка при синхронизации индексов: %s", e)
