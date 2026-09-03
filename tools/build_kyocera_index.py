import re
import json
import os
import argparse
from datetime import datetime

def build_index(inf_path: str, output_path: str):
    print(f"Reading INF file from: {inf_path}")
    
    models_dict = {}
    
    # Регулярка для поиска строк вида: "Kyocera ECOSYS M2040dn KX" = ...
    # Мы извлекаем точное имя драйвера, а в качестве ключа будем использовать имя модели (вырезав "Kyocera " и " KX")
    pattern = re.compile(r'^"(Kyocera\s+(.+?)\s+KX)"\s*=')
    
    with open(inf_path, 'r', encoding='utf-16le', errors='ignore') as f:
        # Иногда INF могут быть в другой кодировке, можно попробовать разные, но обычно это utf-16le
        content = f.read()
        
        # Если файл прочитался кракозябрами из-за кодировки:
        if '\x00' not in content and 'Kyocera' not in content:
            # переоткрываем в utf-8
            with open(inf_path, 'r', encoding='utf-8', errors='ignore') as f2:
                content = f2.read()
        
    lines = content.splitlines()
    count = 0
    
    for line in lines:
        line = line.strip()
        match = pattern.search(line)
        if match:
            full_driver_name = match.group(1).strip()
            model_name = match.group(2).strip()
            
            # Устраняем дубликаты (т.к. для одной модели может быть несколько строк с разными Hardware ID)
            if model_name not in models_dict:
                models_dict[model_name] = full_driver_name
                count += 1

    print(f"Found {count} unique printer models in the INF file.")
    
    index_data = {
        "version": "KX UPD Auto-Generated",
        "inf_path": inf_path,
        "generated_at": datetime.now().isoformat(),
        "models": models_dict
    }
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
        
    print(f"Saved index to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Kyocera Driver JSON Index from INF file")
    parser.add_argument("--inf", type=str, default=r"D:\Soft\!Принтеры\Kyocera\KX_UPD\64bit\OEMSETUP.INF", help="Path to Kyocera OEMSETUP.INF")
    parser.add_argument("--out", type=str, default=r"..\printer-worker\knowledge_base\kyocera_driver_index.json", help="Path to output JSON")
    
    args = parser.parse_args()
    build_index(args.inf, args.out)
