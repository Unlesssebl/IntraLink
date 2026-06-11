from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from services.api_client import api_client

router = Router()


class AuthStates(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()


@router.message(Command("login"))
@router.message(F.text == "🔑 Авторизация")
async def cmd_login(message: types.Message, state: FSMContext):
    await message.answer("Пожалуйста, введите ваш логин от IntraService:")
    await state.set_state(AuthStates.waiting_for_login)


@router.message(AuthStates.waiting_for_login)
async def process_login(message: types.Message, state: FSMContext):
    await state.update_data(login=message.text)
    await message.answer("Теперь введите ваш пароль:")
    await state.set_state(AuthStates.waiting_for_password)


@router.message(AuthStates.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    login = data.get("login")
    password = message.text

    # Удаляем сообщение с паролем для безопасности
    try:
        await message.delete()
    except Exception:
        pass

    msg = await message.answer("⏳ Проверяю учетные данные...")

    # Вызываем Core API для авторизации
    response = await api_client.login(message.from_user.id, login, password)

    if response and response.get("status") == "success":
        # Импортируем клавиатуру из start_help для обновления интерфейса
        from handlers.start_help import get_main_keyboard

        # Редактируем старое сообщение
        await msg.edit_text(
            "✅ Авторизация прошла успешно! Теперь я буду мониторить ваши заявки."
        )

        # Отправляем новое сообщение с Reply-клавиатурой
        await message.answer(
            "Теперь вам доступны все функции. Используйте меню ниже.",
            reply_markup=get_main_keyboard(True),
        )
        await state.clear()
    else:
        await msg.edit_text(
            "❌ Ошибка авторизации. Проверьте логин/пароль и попробуйте еще раз."
        )
        await state.clear()


@router.message(Command("logout"))
@router.message(F.text == "🚪 Выйти")
async def cmd_logout(message: types.Message):
    user = await api_client.get_user(message.from_user.id)
    if not user:
        await message.answer("Вы и так не авторизованы.")
        return

    await api_client.logout(message.from_user.id)

    from handlers.start_help import get_main_keyboard

    await message.answer(
        "Вы успешно вышли из системы. Я больше не буду присылать уведомления по вашему профилю.",
        reply_markup=get_main_keyboard(False),
    )
