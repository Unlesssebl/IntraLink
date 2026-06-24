import os
import re
import json
import logging
import io
from debug_tools import PROJECT_ROOT, add_to_path

# Переопределяем путь к базе знаний
kb_path = os.path.join(PROJECT_ROOT, "printer-worker", "knowledge_base", "printers_knowledge_base.json")
os.environ["PRINTERS_KB_PATH"] = kb_path

add_to_path("printer-worker")


def _setup_debug_logging() -> list:
    """Включает DEBUG-логирование для модулей воркера и возвращает буфер строк."""
    log_lines = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            log_lines.append(self.format(record))

    handler = ListHandler()
    handler.setFormatter(logging.Formatter("  [LOG %(levelname)s] %(name)s — %(message)s"))

    for name in ("orchestrator.router", "orchestrator.snmp", "llm"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)

    return log_lines


async def run_printer_debugger(task_data: dict, task_id: int):
    from orchestrator.schemas import PrintJob, JobState, KnowledgeBase

    print("=" * 55)
    print("🖨️  PRINTER-WORKER DEBUGGER  (Dry-Run / No I/O)")
    print("=" * 55)

    # ── 1. Загрузка данных заявки ──────────────────────────────
    target_pc = task_data.get("Field1112") or None
    model_key = task_data.get("Field1103") or None

    if not target_pc and task_data.get("CreatorComments"):
        target_pc = task_data.get("CreatorComments")

    if (not target_pc or not model_key) and task_data.get("Data"):
        data_xml = task_data["Data"]
        if not target_pc:
            m = re.search(r'<field id="1112">([^<]+)</field>', data_xml)
            if m:
                target_pc = m.group(1).strip() or None
        if not model_key:
            m = re.search(r'<field id="1103">([^<]+)</field>', data_xml)
            if m:
                model_key = m.group(1).strip() or None

    raw_text = f"{task_data.get('Name', '')} {task_data.get('Description', '')}"
    if task_data.get("CreatorComments"):
        raw_text += f" {task_data['CreatorComments']}"

    print(f"\nID Заявки : {task_id}")
    print(f"Раздел    : {task_data.get('ServiceName', '—')}")
    print(f"Тема      : {task_data.get('Name', '—')}")
    desc = str(task_data.get('Description', ''))
    print(f"Описание  : {desc[:200]}{'...' if len(desc) > 200 else ''}")
    print(f"\n[RAW ПОЛЯ]")
    print(f"  Field1112 (Target PC raw) : {task_data.get('Field1112')}")
    print(f"  Field1103 (Model Key raw) : {task_data.get('Field1103')}")
    print(f"  CreatorComments           : {task_data.get('CreatorComments')}")
    print(f"  Data XML                  : {'Есть' if task_data.get('Data') else 'Нет'}")

    # ── 2. Применение Sanitization (как в redis_listener) ─────
    if not target_pc or re.fullmatch(r"\d{3,4}", (target_pc or "").strip()):
        m_pc = re.search(r"\b(?:NTMW|TNT|TKT|NTEMW|16-)\s*\d{2,4}\b", raw_text, re.IGNORECASE)
        if m_pc:
            target_pc = m_pc.group(0).upper()

    cyrillic = 'ОСАЕРХМТКВ'
    latin    = 'OCAEPXMTKB'
    tr_map = str.maketrans(cyrillic + cyrillic.lower(), latin + latin.lower())

    if target_pc:
        target_pc = target_pc.translate(tr_map).replace(" ", "").upper()

    if model_key:
        model_key = model_key.translate(tr_map)
        model_key = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', model_key).strip()
        if not model_key:
            model_key = None

    if target_pc:
        raw_text += f" {target_pc}"
    if model_key:
        raw_text += f" {model_key}"

    print(f"\n[1] 🧩 ПОСЛЕ SANITIZATION:")
    print(f"  target_pc  → {target_pc or '❌ не определён'}")
    print(f"  model_key  → {model_key or '❌ не определён'}")

    # ── 3. Инициализация PrintJob и загрузка КБ ───────────────
    from orchestrator.schemas import PrintJob, JobState, KnowledgeBase

    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        kb = KnowledgeBase.model_validate(data)
        print(f"\n[КБ] Загружено {len(kb.printers)} моделей из базы знаний.")
    except Exception as e:
        print(f"❌ Ошибка загрузки базы знаний: {e}")
        return

    job = PrintJob(
        task_id=task_id,
        tg_user_id=0,
        raw_text=raw_text,
        state=JobState.PENDING,
        target_pc=target_pc,
        model_key=model_key,
    )

    # ── 4. Запуск ПОЛНОГО маршрутизатора (все 3 уровня) ───────
    print(f"\n[2] 🗺️  ЗАПУСК JobRouter.route() (SNMP → Fast-Track → Smart-Track)...")
    log_lines = _setup_debug_logging()

    from orchestrator.router import JobRouter
    router = JobRouter(kb)

    try:
        job = await router.route(job)
    except Exception as e:
        print(f"❌ Исключение в router.route(): {e}")

    # Выводим все логи маршрутизатора
    if log_lines:
        print("\n  --- Логи маршрутизатора ---")
        for line in log_lines:
            print(f"  {line}")
        print("  ---")

    # ── 5. Итоговые результаты ────────────────────────────────
    print(f"\n[3] 📊 ИТОГ МАРШРУТИЗАЦИИ:")
    state_icon = {
        "routing": "🔄", "parsing": "🤖", "probing": "🔍",
        "pending": "⏳", "failed": "❌", "done": "✅", "waiting_approval": "⏸️"
    }.get(job.state.value, "❓")
    print(f"  Состояние     : {state_icon} {job.state.value}")
    print(f"  target_pc     : {job.target_pc or '—'}")
    print(f"  model_key     : {job.model_key or '—'}")
    print(f"  connection    : {job.connection_type.value if job.connection_type else '—'}")
    print(f"  printer_addr  : {job.printer_address or '—'}")

    if job.driver_info:
        d = job.driver_info
        print(f"\n  ✅ Драйвер найден:")
        print(f"     display_name  : {d.display_name}")
        print(f"     driver_name   : {d.driver_name}")
        print(f"     driver_bundle : {d.driver_bundle or '—'}")
        print(f"     inf_path      : {d.driver_inf_path}")
        if d.driver_bundle:
            print(f"     ⚡ Тип подбора: через бандл '{d.driver_bundle}'")
        else:
            print(f"     ⚡ Тип подбора: прямой ключ из КБ")
    else:
        print(f"\n  ❌ Драйвер НЕ определён.")

    if job.error_message:
        print(f"\n  ⚠️  Сообщение об ошибке: {job.error_message}")
