import json
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import redis.asyncio as aioredis
from config import REDIS_URL

router = Router(name="printer_approvals")

class PrinterSetup(StatesGroup):
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
    await redis.close() # Обязательно закрываем соединение после публикации

# ==========================================
# НОВЫЕ ОБРАБОТЧИКИ НАЖАТИЙ НА КНОПКИ (CALLBACKS)
# ==========================================

@router.callback_query(F.data.startswith("printer_approve:"))
async def process_approve_button(callback: CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    
    await _publish_response(task_id, action="approve")
    
    # Меняем текст и убираем кнопки
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
    
    # Сохраняем task_id в FSM
    await state.update_data(task_id=task_id)
    
    # Убираем кнопки из оригинального сообщения, чтобы их больше не нажимали
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
            
    # Отправляем update
    update_kwargs = {}
    if "target_pc" in data: update_kwargs["target_pc"] = data["target_pc"]
    if "model_key" in data: update_kwargs["model_key"] = data["model_key"]
    if "connection_type" in data: update_kwargs["connection_type"] = data["connection_type"]
    
    await _publish_response(task_id, action="edit", **update_kwargs)
    await message.answer(f"✅ Параметры для задачи #{task_id} обновлены и отправлены воркеру.")
    await state.clear()