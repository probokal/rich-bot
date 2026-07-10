"""
main.py — точка входа. Регистрируем бота aiogram 3, меню-конструктор
и обработчики. Запуск: python main.py
"""
import asyncio
import time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_TOKEN, ALLOWED_USER_IDS
from db import init_db, save_draft, load_draft, save_template, list_templates, load_template, log_sent
from rich_sender import send_rich_message
from inline_parser import parse_inline

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ── Простая авторизация: бот отвечает только разрешённым user_id ──
def allowed(user_id: int) -> bool:
    return (not ALLOWED_USER_IDS) or (user_id in ALLOWED_USER_IDS)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not allowed(message.from_user.id):
        return  # молча игнорируем чужих
    await message.answer(
        "👋 Привет! Я конструктор Rich-постов.\n"
        "Жми «➕ Добавить блок» и собирай пост по частям.",
        reply_markup=main_menu(),
    )


# ── Состояния конструктора ──
class Builder(StatesGroup):
    waiting_text = State()      # ждём текст для абзаца/заголовка
    waiting_list = State()      # ждём пункты списка
    waiting_send = State()      # ждём chat_id для отправки


# Хранилище черновика в памяти пользователя (в FSM data).
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить блок", callback_data="add")],
        [
            InlineKeyboardButton(text="👁 Предпросмотр", callback_data="preview"),
            InlineKeyboardButton(text="📤 Отправить", callback_data="send"),
        ],
        [
            InlineKeyboardButton(text="💾 Сохранить", callback_data="save"),
            InlineKeyboardButton(text="📂 Шаблоны", callback_data="templates"),
        ],
        [InlineKeyboardButton(text="🗑 Сбросить", callback_data="reset")],
    ])


def add_menu():
    kb = [
        [InlineKeyboardButton(text="Заголовок", callback_data="add:heading")],
        [InlineKeyboardButton(text="Абзац", callback_data="add:paragraph")],
        [InlineKeyboardButton(text="Список", callback_data="add:list")],
        [InlineKeyboardButton(text="Цитата", callback_data="add:quote")],
        [InlineKeyboardButton(text="Таблица", callback_data="add:table")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ── Главное меню ──
@dp.callback_query(F.data == "back")
async def back(cb: types.CallbackQuery):
    await cb.message.edit_text("Конструктор:", reply_markup=main_menu())
    await cb.answer()


@dp.callback_query(F.data == "add")
async def add(cb: types.CallbackQuery):
    await cb.message.edit_text("Какой блок добавить?", reply_markup=add_menu())
    await cb.answer()


# ── Добавление текстовых блоков ──
@dp.callback_query(F.data.in_(["add:heading", "add:paragraph"]))
async def add_text(cb: types.CallbackQuery, state: FSMContext):
    kind = "heading" if cb.data == "add:heading" else "paragraph"
    await state.update_data(pending=kind)
    await state.set_state(Builder.waiting_text)
    await cb.message.answer("Отправьте текст. Можно форматировать: *жирный* _курсив_ `код` ~~зачёрк~~ ==выд== ||спойлер||")
    await cb.answer()


@dp.message(Builder.waiting_text)
async def got_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kind = data["pending"]
    segs = parse_inline(message.text)
    # Превращаем сегменты в markdown-строку блока
    md = "# " + message.text if kind == "heading" else message.text
    blocks = (await state.get_data()).get("blocks", [])
    blocks.append(md)
    await state.update_data(blocks=blocks)
    # Убираем только текущее состояние, но сохраняем собранные блоки в FSM data.
    await state.set_state(None)
    await message.answer("✅ Добавлено! Продолжаем:", reply_markup=main_menu())


# ── Предпросмотр: реально шлём пост самому себе ──
@dp.callback_query(F.data == "preview")
async def preview(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    blocks = data.get("blocks", [])
    if not blocks:
        await cb.answer("Пост пуст 🤷", show_alert=True)
        return
    markdown = "\n\n".join(blocks)
    await send_rich_message(cb.from_user.id, markdown=markdown)
    await cb.answer("Отправил превью вам в чат!")


# ── Отправка в канал/группу/личку ──
@dp.callback_query(F.data == "send")
async def send_menu(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(Builder.waiting_send)
    await cb.message.answer(
        "Куда отправить? Пришлите chat_id (число), @username канала "
        "или ссылку-сокращение. Для лички по ID — просто число user_id."
    )
    await cb.answer()


@dp.message(Builder.waiting_send)
async def do_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    blocks = data.get("blocks", [])
    markdown = "\n\n".join(blocks)
    raw_chat_id = message.text.strip()
    # Числовой ID лички/группы передаём как int, а @username канала оставляем строкой.
    chat_id = int(raw_chat_id) if raw_chat_id.lstrip("-").isdigit() else raw_chat_id
    # protect_content и disable_notification можно сделать кнопками;
    # здесь — пример с защитой контента:
    result = await send_rich_message(
        chat_id, markdown=markdown, protect_content=False, disable_notification=False
    )
    if result.get("ok"):
        msg = result["result"]
        await log_sent(chat_id, msg["message_id"], "post")
        await message.answer(f"✅ Отправлено! message_id = {msg['message_id']}")
    else:
        await message.answer(f"❌ Ошибка: {result}")
    await state.clear()


# ── Сохранение черновика в SQLite ──
@dp.callback_query(F.data == "save")
async def save(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    blocks = data.get("blocks", [])
    if not blocks:
        await cb.answer("Нечего сохранять", show_alert=True)
        return
    markdown = "\n\n".join(blocks)
    await save_draft(cb.from_user.id, markdown, "")
    await cb.answer("💾 Черновик сохранён в БД")


# ── Шаблоны ──
@dp.callback_query(F.data == "templates")
async def templates(cb: types.CallbackQuery):
    names = await list_templates()
    text = "Шаблоны:\n" + ("\n".join(f"• {n}" for n in names) if names else "(пусто)")
    await cb.message.edit_text(text, reply_markup=main_menu())
    await cb.answer()


# ── Сброс ──
@dp.callback_query(F.data == "reset")
async def reset(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("🗑 Конструктор очищен.", reply_markup=main_menu())
    await cb.answer()


# ── Запуск ──
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
