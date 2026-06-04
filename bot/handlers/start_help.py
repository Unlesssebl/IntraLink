from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from services.api_client import api_client

router = Router()

def get_main_keyboard(is_auth: bool):
    builder = ReplyKeyboardBuilder()
    if is_auth:
        builder.button(text="📋 Мои заявки")
        builder.button(text="🚪 Выйти")
    else:
        builder.button(text="🔑 Авторизация")
    
    builder.button(text="❓ Помощь")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user = await api_client.get_user(message.from_user.id)
    is_auth = bool(user and user.get("is_login"))
    
    if is_auth:
        text = (
            f"👋 С возвращением! Вы авторизованы под логином <b>{user.get('is_login')}</b>.\n\n"
            "Я продолжаю мониторить ваши заявки в фоновом режиме.\n"
            "Используйте кнопки меню ниже для управления."
        )
    else:
        text = (
            "Привет! Я бот для мониторинга заявок в IntraService.\n\n"
            "Чтобы начать получать уведомления, пожалуйста, пройдите <b>авторизацию</b>."
        )
    
    await message.answer(
        text,
        reply_markup=get_main_keyboard(is_auth),
        parse_mode="HTML"
    )

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: types.Message):
    await message.answer(
        "Я помогаю отслеживать заявки в системе IntraService.\n\n"
        "🔹 <b>Авторизация</b> — вход в систему\n"
        "🔹 <b>Мои заявки</b> — список ваших последних задач\n"
        "🔹 <b>Выйти</b> — отключение уведомлений и удаление сессии\n\n"
        "Я буду автоматически уведомлять вас о новых заявках и новых комментариях в тех задачах, где вы назначены исполнителем.",
        parse_mode="HTML"
    )
