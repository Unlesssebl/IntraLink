import os
import re
import json
from debug_tools import PROJECT_ROOT, add_to_path

# Переопределяем путь к базе знаний
kb_path = os.path.join(PROJECT_ROOT, "printer-worker", "knowledge_base", "printers_knowledge_base.json")
os.environ["PRINTERS_KB_PATH"] = kb_path

add_to_path("printer-worker")

from orchestrator.schemas import PrintJob, JobState, KnowledgeBase

async def run_printer_debugger(task_data: dict, task_id: int):
    print("="*50)
    print("🖨️ PRINTER-WORKER DEBUGGER (Dry-Run)")
    print("="*50)
    
    target_pc = task_data.get("Field1112") or None
    model_key = task_data.get("Field1103") or None

    if not target_pc and task_data.get("CreatorComments"):
        target_pc = task_data.get("CreatorComments")

    if (not target_pc or not model_key) and task_data.get("Data"):
        data_xml = task_data["Data"]
        if not target_pc:
            m = re.search(r'<field id="1112">([^<]+)</field>', data_xml)
            if m: target_pc = m.group(1).strip() or None
        if not model_key:
            m = re.search(r'<field id="1103">([^<]+)</field>', data_xml)
            if m: model_key = m.group(1).strip() or None

    raw_text = f"{task_data.get('Name', '')} {task_data.get('Description', '')}"
    if task_data.get("CreatorComments"):
        raw_text += f" {task_data['CreatorComments']}"

    if not target_pc or re.fullmatch(r"\d{3,4}", target_pc.strip()):
        m_pc = re.search(r"\b(?:NTMW|TNT|TKT|NTEMW|16-)\s*\d{2,4}\b", raw_text, re.IGNORECASE)
        if m_pc:
            target_pc = m_pc.group(0).upper()

    if target_pc:
        cyrillic = 'ОСАЕРХМТКВ'
        latin    = 'OCAEPXMTKB'
        tr_map = str.maketrans(cyrillic + cyrillic.lower(), latin + latin.lower())
        target_pc = target_pc.translate(tr_map).replace(" ", "").upper()

    if model_key:
        cyrillic = 'ОСАЕРХМТКВ'
        latin    = 'OCAEPXMTKB'
        tr_map = str.maketrans(cyrillic + cyrillic.lower(), latin + latin.lower())
        model_key = model_key.translate(tr_map)
        model_key = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', model_key)
        model_key = model_key.strip()
        if not model_key:
            model_key = None

    if target_pc: raw_text += f" {target_pc}"
    if model_key: raw_text += f" {model_key}"

    print(f"ID Заявки: {task_id}")
    print(f"Исходные поля:")
    print(f"  Field1112 (Target PC): {task_data.get('Field1112')}")
    print(f"  Field1103 (Model Key): {task_data.get('Field1103')}")
    print(f"  CreatorComments: {task_data.get('CreatorComments')}")
    print(f"  Data XML (наличие): {'Да' if task_data.get('Data') else 'Нет'}")

    print("\n[1] 🧩 Извлечение данных:")
    print(f"  Целевой ПК (target_pc): {target_pc}")
    print(f"  Ключ модели (model_key): {model_key}")
    
    job = PrintJob(
        task_id=task_id,
        tg_user_id=0,
        raw_text=raw_text,
        state=JobState.PENDING,
        target_pc=target_pc,
        model_key=model_key,
    )

    print("\n[2] 📚 Поиск в Базе Знаний (Knowledge Base)...")
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        kb = KnowledgeBase.model_validate(data)

        if job.model_key:
            job.driver_info = kb.find_by_key(job.model_key)
            if job.driver_info:
                print("✅ Принтер найден в базе:")
                print(f"  Ключ (Key): {job.driver_info.key}")
                print(f"  Название (Display Name): {job.driver_info.display_name}")
                print(f"  Тип подключения: {job.driver_info.connection_type.value}")
                print(f"  Драйвер: {job.driver_info.driver_folder} / {job.driver_info.inf_file}")
            else:
                print("❌ Принтер НЕ найден в базе!")
        else:
            print("❌ Ключ модели (model_key) не извлечен, поиск в базе невозможен.")
    except Exception as e:
        print(f"❌ Ошибка при работе с базой знаний: {e}")
