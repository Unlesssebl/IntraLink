from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from services.api_client import api_client

router = Router()


def get_main_keyboard(is_auth: bool):
    builder = ReplyKeyboardBuilder()
    if is_auth:
        builder.button(text="📋 Мои заявки")
        builder.button(text="🖨 Принтеры")  # <-- НОВАЯ КНОПКА ДОБАВЛЕНА СЮДА
        builder.button(text="🚪 Выйти")
    else:
        builder.button(text="🔑 Авторизация")

    builder.button(text="❓ Помощь")

    # Метод adjust(2) выстроит кнопки по 2 в ряд.
    # Для авторизованного пользователя получится аккуратная сетка 2x2.
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
        text, reply_markup=get_main_keyboard(is_auth), parse_mode="HTML"
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: types.Message):
    await message.answer(
        "Я помогаю отслеживать заявки в системе IntraService.\n\n"
        "<b>Доступные действия:</b>\n"
        "• 📋 <b>Мои заявки</b> — посмотреть список активных задач\n"
        "• 🖨 <b>Принтеры</b> — панель управления автоматической установкой принтеров\n"
        "• 🔑 <b>Авторизация/Выйти</b> — управление вашим аккаунтом",
        parse_mode="HTML",
    )
