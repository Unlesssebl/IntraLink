import json
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

import redis.asyncio as aioredis
from config import REDIS_URL
from services.api_client import api_client

router = Router(name="printer_approvals")

# Схемы для коллбеков
class PrinterTaskSelect(CallbackData, prefix="prn_tsk"):
    task_id: int

class PrinterManualId(CallbackData, prefix="prn_man"):
    pass

class PrinterSetup(StatesGroup):
    waiting_for_task_id = State()
    waiting_for_pc = State()
    waiting_for_driver = State()
    waiting_for_connection = State()

def get_approval_keyboard(task_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✅ Установить (Авто)", callback_data=f"printer_approve:{task_id}")],
        [InlineKeyboardButton(text="❌ Отменить (Ручной разбор)", callback_data=f"printer_reject:{task_id}")],
        [InlineKeyboardButton(text="⚙️ Изменить параметры", callback_data=f"printer_edit:{task_id}")],
        [InlineKeyboardButton(text="💬 Запросить действие у пользователя", callback_data=f"printer_ask_user:{task_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def _publish_response(task_id: int, action: str, **kwargs):
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    payload = {"event_type": "approval_response", "task_id": task_id, "action": action}
    payload.update(kwargs)
    await redis.publish("printer_actions", json.dumps(payload))
    await redis.close()


# ==========================================
# УМНОЕ МЕНЮ ВЫБОРА ЗАЯВОК
# ==========================================

@router.message(F.text == "🖨 Принтеры")
async def cmd_printers_menu(message: Message, state: FSMContext):
    await state.clear()
    msg = await message.answer("⏳ Загружаю ваши активные заявки...")
    
    # 1. Получаем ID пользователя
    user = await api_client.get_user(message.from_user.id)
    if not user or not user.get("is_login"):
        await msg.edit_text("❌ Ошибка: вы не авторизованы.")
        return
        
    user_id = user.get("is_user_id")
    
    # 2. Получаем последние 10 заявок, где пользователь - исполнитель
    filters = {"pagesize": 10, "ExecutorIds": str(user_id)} 
    tasks_data = await api_client.get_tasks(message.from_user.id, filters)
    
    tasks = []
    if isinstance(tasks_data, list):
        tasks = tasks_data
    elif isinstance(tasks_data, dict):
        tasks = tasks_data.get("Tasks", [])
        
    builder = InlineKeyboardBuilder()
    
    # 3. Рисуем кнопки для каждой задачи
    if tasks:
        for task in tasks:
            # Обрезаем слишком длинные названия заявок
            name = task.get("Name", "Без названия")
            short_name = name[:30] + "..." if len(name) > 30 else name
            btn_text = f"#{task['Id']} — {short_name}"
            
            builder.button(
                text=btn_text, 
                callback_data=PrinterTaskSelect(task_id=task['Id'])
            )
            
    # 4. Добавляем кнопку ручного ввода в самый низ
    builder.button(text="✍️ Ввести ID вручную", callback_data=PrinterManualId())
    builder.adjust(1) # Выстраиваем кнопки в один столбец
    
    text = "🖨 <b>Панель управления принтерами</b>\n\nВыберите заявку из списка ниже или введите номер вручную:"
    await msg.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(PrinterTaskSelect.filter())
async def process_printer_task_select(callback: CallbackQuery, callback_data: PrinterTaskSelect):
    """Срабатывает при клике на заявку из списка"""
    task_id = callback_data.task_id
    await callback.message.edit_text(
        f"📋 <b>Управление заявкой #{task_id}</b>\n\nВыберите действие для установки принтера:",
        parse_mode="HTML",
        reply_markup=get_approval_keyboard(task_id)
    )
    await callback.answer()


@router.callback_query(PrinterManualId.filter())
async def process_printer_manual_id(callback: CallbackQuery, state: FSMContext):
    """Срабатывает при клике на 'Ввести ID вручную'"""
    await callback.message.edit_text(
        "⌨️ <b>Ручной ввод</b>\n\nОтправьте мне номер заявки (ID) в виде числа:",
        parse_mode="HTML"
    )
    await state.set_state(PrinterSetup.waiting_for_task_id)
    await callback.answer()


@router.message(PrinterSetup.waiting_for_task_id)
async def process_manual_task_id(message: Message, state: FSMContext):
    """Ловит напечатанный ID заявки"""
    if not message.text.isdigit():
        await message.answer("⚠️ Ошибка: Номер заявки должен содержать только цифры. Попробуйте еще раз:")
        return
        
    task_id = int(message.text.strip())
    
    await message.answer(
        f"📋 <b>Ручное управление заявкой #{task_id}</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=get_approval_keyboard(task_id)
    )
    await state.clear()


# ==========================================
# ОБРАБОТЧИКИ НАЖАТИЙ НА КНОПКИ ДЕЙСТВИЙ
# ==========================================

@router.callback_query(F.data.startswith("printer_approve:"))
async def process_approve_button(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    await _publish_response(task_id, action="approve")
    await callback.message.edit_text(f"{callback.message.text}\n\n✅ <b>Статус:</b> Отправлено на автоматическую установку.")
    await callback.answer("Заявка одобрена")

@router.callback_query(F.data.startswith("printer_reject:"))
async def process_reject_button(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    await _publish_response(task_id, action="reject")
    await callback.message.edit_text(f"{callback.message.text}\n\n❌ <b>Статус:</b> Переведено в ручной режим разбора.")
    await callback.answer("Установка отменена")

@router.callback_query(F.data.startswith("printer_edit:"))
async def process_edit_button(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.update_data(task_id=task_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Введите имя целевого ПК (или 'пропустить'):")
    await state.set_state(PrinterSetup.waiting_for_pc)
    await callback.answer()

@router.callback_query(F.data.startswith("printer_ask_user:"))
async def process_ask_user_button(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    await _publish_response(task_id, action="ask_user")
    await callback.message.edit_text(f"{callback.message.text}\n\n💬 <b>Статус:</b> Запрошено действие у пользователя.")
    await callback.answer("Запрос отправлен")

# ==========================================
# ОБРАБОТЧИКИ FSM (РУЧНОЙ ВВОД ПАРАМЕТРОВ)
# ==========================================

@router.message(PrinterSetup.waiting_for_pc)
async def process_pc(message: Message, state: FSMContext):
    if not message.text: return
    if message.text.lower() != 'пропустить':
        await state.update_data(target_pc=message.text.strip())
    await message.answer("Введите точное имя драйвера (или 'пропустить'):")
    await state.set_state(PrinterSetup.waiting_for_driver)

@router.message(PrinterSetup.waiting_for_driver)
async def process_driver(message: Message, state: FSMContext):
    if not message.text: return
    if message.text.lower() != 'пропустить':
        await state.update_data(model_key=message.text.strip())
    await message.answer("Введите тип подключения (usb или tcpip) или 'пропустить':")
    await state.set_state(PrinterSetup.waiting_for_connection)

@router.message(PrinterSetup.waiting_for_connection)
async def process_connection(message: Message, state: FSMContext):
    if not message.text: return
    data = await state.get_data()
    task_id_raw = data.get("task_id")
    if not task_id_raw:
        await message.answer("Ошибка: потерян контекст задачи.")
        await state.clear()
        return
        
    task_id = int(task_id_raw)
    
    if message.text.lower() != 'пропустить':
        conn_type = message.text.strip().lower()
        if conn_type in ("usb", "tcpip"):
            data["connection_type"] = conn_type
            
    update_kwargs = {}
    if "target_pc" in data: update_kwargs["target_pc"] = data["target_pc"]
    if "model_key" in data: update_kwargs["model_key"] = data["model_key"]
    if "connection_type" in data: update_kwargs["connection_type"] = data["connection_type"]
    
    await _publish_response(task_id, action="edit", **update_kwargs)
    await message.answer(f"✅ Параметры для задачи #{task_id} обновлены и отправлены воркеру.")
    await state.clear()