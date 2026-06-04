from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from services.api_client import api_client
from config import INTRAService_URL

router = Router()

class TicketsPagination(CallbackData, prefix="tickets"):
    page: int

def get_status_emoji(status_name):
    name = str(status_name).lower()
    if "закрыт" in name or "решен" in name:
        return "🟢"
    elif "в работе" in name or "назначен" in name:
        return "🟡"
    elif "открыт" in name or "нов" in name:
        return "🔵"
    return "⚪"

async def build_tickets_page(tg_user_id: int, user_id: int, page: int = 1, pagesize: int = 5):
    # 1. Получаем список всех статусов для фильтрации
    statuses_data = await api_client.get_statuses(tg_user_id)
    active_status_ids = []
    if isinstance(statuses_data, list):
        for s in statuses_data:
            if not s.get("IsFinal") and not s.get("IsFixed"):
                active_status_ids.append(str(s.get("Id")))
    
    # Формируем фильтры
    filters = {
        "page": page, 
        "pagesize": pagesize,
        "ExecutorIds": str(user_id)
    }
    
    if active_status_ids:
        filters["StatusIds"] = ",".join(active_status_ids)

    # get_tasks через Core API
    tasks_data = await api_client.get_tasks(tg_user_id, filters)
    
    if tasks_data is None:
        return "❌ Не удалось получить список заявок.", None
    
    tasks = []
    statuses_map = {}
    
    if isinstance(tasks_data, list):
        tasks = tasks_data
    elif isinstance(tasks_data, dict):
        tasks = tasks_data.get("Tasks", [])
        # Если API вернул блок Statuses (благодаря include=status), мапим его
        for s in tasks_data.get("Statuses", []):
            statuses_map[s.get("Id")] = s.get("Name")
    
    if not tasks and page == 1:
        return "У вас нет активных заявок, где вы являетесь исполнителем.", None
    elif not tasks:
        return f"На странице {page} нет подходящих заявок.", None
    
    # Формируем текст сообщения
    text = f"📋 <b>Ваши активные заявки (Страница {page})</b>\n"
    text += f"<i>(Вы назначены исполнителем)</i>\n\n"
    
    base_web_url = INTRAService_URL.replace("/api/", "")
    
    for task in tasks:
        # Пытаемся взять StatusName из задачи, если его нет - из мапы статусов, иначе N/A
        status = task.get('StatusName')
        if not status and task.get('StatusId') in statuses_map:
            status = statuses_map[task.get('StatusId')]
        
        status = status or 'N/A'
        
        emoji = get_status_emoji(status)
        text += f"<b>#{task['Id']}</b> — {task['Name']}\n"
        text += f"├ Статус: {emoji} <i>{status}</i>\n"
        text += f"└ 🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>\n\n"
        
    builder = InlineKeyboardBuilder()
    if page > 1:
        builder.button(text="⬅️ Назад", callback_data=TicketsPagination(page=page - 1))
    
    if len(tasks) == pagesize:
        builder.button(text="Вперед ➡️", callback_data=TicketsPagination(page=page + 1))
        
    builder.adjust(2)
    
    return text, builder.as_markup()

@router.message(Command("mytickets"))
@router.message(F.text == "📋 Мои заявки")
async def cmd_mytickets(message: types.Message):
    user = await api_client.get_user(message.from_user.id)
    if not user or not user.get("is_login"):
        from handlers.start_help import get_main_keyboard
        await message.answer("Вы не авторизованы.", reply_markup=get_main_keyboard(False))
        return
    
    user_id = user.get("is_user_id")
    if not user_id:
        await message.answer("❌ Ошибка: ваш внутренний ID не найден. Пожалуйста, перезайдите через Выйти -> Авторизация")
        return

    msg = await message.answer("⏳ Загружаю ваши активные заявки...")
    
    text, reply_markup = await build_tickets_page(message.from_user.id, user_id, page=1)
    await msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(TicketsPagination.filter())
async def process_tickets_pagination(callback: types.CallbackQuery, callback_data: TicketsPagination):
    user = await api_client.get_user(callback.from_user.id)
    if not user or not user.get("is_login") or not user.get("is_user_id"):
        await callback.answer("Сессия истекла или данные неполные.", show_alert=True)
        return
        
    user_id = user.get("is_user_id")
    text, reply_markup = await build_tickets_page(callback.from_user.id, user_id, page=callback_data.page)
    
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()
