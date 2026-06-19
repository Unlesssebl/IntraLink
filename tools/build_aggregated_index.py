import os
import re
import json
import argparse
import subprocess
from datetime import datetime

def is_valid_inf_path(path: str) -> bool:
    path_lower = path.lower()
    # Игнорируем старые ОС
    invalid_keywords = ["xp", "vista", "win2000", "ia64", "itanium", "win9x", "nt4"]
    for kw in invalid_keywords:
        # Проверяем как отдельные токены, чтобы не отсеять случайные совпадения,
        # но для надежности пути драйверов часто имеют имена папок вроде "WinXP"
        if kw in path_lower:
            return False
    return True

def get_arch_score(path: str) -> int:
    path_lower = path.lower()
    # Чем выше score, тем лучше
    if "x64" in path_lower or "amd64" in path_lower or "64bit" in path_lower or "win64" in path_lower:
        return 10
    if "x86" in path_lower or "i386" in path_lower or "32bit" in path_lower or "win32" in path_lower:
        return 1
    return 5  # Неизвестно (например, универсальный)

def parse_inf_for_models(inf_path: str):
    models = set()
    # Пытаемся найти определения моделей: "Model Name" = Section, HWID
    pattern = re.compile(r'^"([^"]+)"\s*=\s*[^,]+,')
    
    try:
        with open(inf_path, 'r', encoding='utf-16le', errors='ignore') as f:
            content = f.read()
        # Если файл не utf-16le (в нем нет нулевых байтов), переоткрываем в utf-8
        if '\x00' not in content and 'Manufacturer' not in content:
            with open(inf_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
        in_model_section = False
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            if line.startswith('[') and line.endswith(']'):
                section_name = line[1:-1].lower()
                # Служебные секции пропускаем
                if section_name in ("version", "manufacturer", "strings", "sourcedisksnames", "sourcedisksfiles", "destinationdirs"):
                    in_model_section = False
                else:
                    # В INF принтеров секции моделей часто называются [HP.NTAMD64] и т.д.
                    in_model_section = True
                continue
                
            if in_model_section:
                match = pattern.search(line)
                if match:
                    model_name = match.group(1).strip()
                    # Исключаем переменные вида %PrinterName% (они резолвятся в [Strings])
                    if len(model_name) > 3 and not model_name.startswith('%'):
                        models.add(model_name)
    except Exception as e:
        print(f"Error parsing {inf_path}: {e}")
    return models

def auto_extract_installers(base_dir: str):
    print(f"Checking for unextracted installers in {base_dir}...")
    
    directories = []
    for root, dirs, files in os.walk(base_dir):
        directories.append((root, dirs, files))
        
    for root, dirs, files in directories:
        has_inf = False
        for f in files:
            if f.lower().endswith('.inf'):
                has_inf = True
                break
                
        # Если папка называется _extracted или содержит ее, считаем распакованной
        if '_extracted' in dirs or os.path.basename(root) == '_extracted':
            has_inf = True
            
        if not has_inf:
            installers = [f for f in files if f.lower().endswith(('.exe', '.zip', '.cab'))]
            if installers:
                # Берём самый большой файл, так как драйвер-установщик обычно самый тяжелый
                installers.sort(key=lambda x: os.path.getsize(os.path.join(root, x)), reverse=True)
                target_installer = installers[0]
                installer_path = os.path.join(root, target_installer)
                extracted_dir = os.path.join(root, '_extracted')
                
                print(f"Auto-extracting: {installer_path} -> {extracted_dir}")
                os.makedirs(extracted_dir, exist_ok=True)
                
                try:
                    # Попытка вызвать '7z'
                    cmd = ['7z', 'x', f'-o{extracted_dir}', '-y', installer_path]
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"Successfully extracted {target_installer}")
                except Exception as e:
                    print(f"Failed to extract {target_installer}: {e}")

def build_index(base_dir: str, output_path: str):
    auto_extract_installers(base_dir)
    print(f"Scanning directory: {base_dir}")
    
    # model_name -> { "driver_name": str, "inf_path_suffix": str, "arch_score": int, "mtime": float }
    models_dict = {}
    
    count_files = 0
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.lower().endswith('.inf'):
                full_path = os.path.join(root, file)
                if not is_valid_inf_path(full_path):
                    continue
                
                count_files += 1
                found_models = parse_inf_for_models(full_path)
                if not found_models:
                    continue
                
                rel_path = os.path.relpath(full_path, base_dir)
                arch_score = get_arch_score(rel_path)
                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    mtime = 0
                    
                for model_name in found_models:
                    if model_name in models_dict:
                        curr = models_dict[model_name]
                        # Эвристика дедупликации: архитектура -> дата
                        if arch_score > curr["arch_score"]:
                            replace = True
                        elif arch_score == curr["arch_score"] and mtime > curr["mtime"]:
                            replace = True
                        else:
                            replace = False
                    else:
                        replace = True
                        
                    if replace:
                        models_dict[model_name] = {
                            "driver_name": model_name,
                            "inf_path_suffix": rel_path,
                            "arch_score": arch_score,
                            "mtime": mtime
                        }

    print(f"Scanned {count_files} valid INF files.")
    print(f"Found {len(models_dict)} unique printer models.")
    
    # Подготавливаем финальный JSON
    final_models = {}
    for k, v in models_dict.items():
        final_models[k] = {
            "driver_name": v["driver_name"],
            "inf_path_suffix": v["inf_path_suffix"].replace('\\', '/')
        }
        
    index_data = {
        "version": "Aggregated UPD Index 1.0",
        "base_dir": base_dir,
        "generated_at": datetime.now().isoformat(),
        "models": final_models
    }
    
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
        
    print(f"Saved index to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Aggregated JSON Index from multiple INF files")
    parser.add_argument("--dir", type=str, required=True, help="Path to base driver directory (e.g. \\\\truenas\\Drivers\\printer\\Hp)")
    parser.add_argument("--out", type=str, required=True, help="Path to output JSON")
    
    args = parser.parse_args()
    build_index(args.dir, args.out)
