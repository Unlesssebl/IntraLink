from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from services.api_client import api_client

router = Router()


class AuthStates(StatesGroup):
    waiting_for_link_code = State()


@router.message(Command("login"))
@router.message(F.text == "🔑 Авторизация")
async def cmd_login(message: types.Message, state: FSMContext):
    await message.answer(
        "Откройте панель оператора, создайте одноразовый код привязки Telegram "
        "и отправьте его сюда. Код действует 10 минут."
    )
    await state.set_state(AuthStates.waiting_for_link_code)


@router.message(AuthStates.waiting_for_link_code)
async def process_link_code(message: types.Message, state: FSMContext):
    msg = await message.answer("Проверяю одноразовый код...")
    response = await api_client.link_telegram(message.from_user.id, message.text.strip())

    if response and response.get("status") == "success":
        # Импортируем клавиатуру из start_help для обновления интерфейса
        from handlers.start_help import get_main_keyboard

        # Редактируем старое сообщение
        await msg.edit_text(
            "Telegram успешно привязан к вашей корпоративной учётной записи."
        )

        # Отправляем новое сообщение с Reply-клавиатурой
        await message.answer(
            "Теперь вам доступны все функции. Используйте меню ниже.",
            reply_markup=get_main_keyboard(True),
        )
        await state.clear()
    else:
        await msg.edit_text(
            "Код недействителен или истёк. Создайте новый код в панели оператора."
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
