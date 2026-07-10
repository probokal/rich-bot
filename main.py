"""
main.py - полный чат-конструктор Rich-постов на aiogram 3.

Пользователь выбирает тип блока кнопкой, присылает данные, а бот собирает
официальный Rich Markdown. Медиа в Rich-сообщениях задаются публичными
HTTP/HTTPS-ссылками: Telegram определяет тип по MIME и URL.
"""
import asyncio
import html
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ALLOWED_USER_IDS, BOT_TOKEN
from db import (
    init_db,
    list_templates,
    load_draft,
    load_template,
    log_sent,
    save_draft,
    save_template,
)
from rich_sender import TelegramAPIError, edit_rich_message, send_rich_message


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Builder(StatesGroup):
    """Состояния диалога конструктора."""

    waiting_block = State()
    waiting_destination = State()
    waiting_template_name = State()


class AuthMiddleware(BaseMiddleware):
    """Не пропускает пользователей, которых нет в ALLOWED_USER_IDS."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user and ALLOWED_USER_IDS and user.id not in ALLOWED_USER_IDS:
            if isinstance(event, types.CallbackQuery):
                await event.answer("Доступ запрещен", show_alert=True)
            elif isinstance(event, types.Message):
                await event.answer("Доступ к этому боту запрещен.")
            return None
        return await handler(event, data)


dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Добавить блок", "menu:add")],
            [button("Предпросмотр", "preview"), button("Отправить", "menu:send")],
            [button("Сохранить черновик", "draft:save"), button("Загрузить", "draft:load")],
            [button("Шаблоны", "menu:templates"), button("Отменить последний", "undo")],
            [button("Очистить", "reset"), button("Помощь", "help")],
        ]
    )


def add_text_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Заголовок H1-H6", "build:heading"), button("Абзац", "build:paragraph")],
            [button("Разделитель", "instant:divider"), button("Подвал", "build:footer")],
            [button("Маркированный список", "build:list_ul")],
            [button("Нумерованный список", "build:list_ol")],
            [button("Список с флажками", "build:list_check")],
            [button("Цитата", "build:blockquote"), button("Pull-цитата", "build:pullquote")],
            [button("Код-блок", "build:code"), button("Details", "build:details")],
            [button("Медиа", "menu:media"), button("Дополнительно", "menu:advanced")],
            [button("Назад", "back")],
        ]
    )


def media_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Фото", "build:photo"), button("Видео", "build:video")],
            [button("Музыка / аудио", "build:audio"), button("GIF", "build:animation")],
            [button("Голосовая заметка", "build:voice")],
            [button("Коллаж", "build:collage"), button("Слайдшоу", "build:slideshow")],
            [button("Назад к блокам", "menu:add"), button("Главное меню", "back")],
        ]
    )


def advanced_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Таблица", "build:table"), button("Формула LaTeX", "build:math")],
            [button("Карта", "build:map"), button("Сноска", "build:footnote")],
            [button("Ссылка / упоминание", "build:link"), button("Якорь", "build:anchor")],
            [button("Кастомный эмодзи", "build:emoji"), button("Дата / время", "build:time")],
            [button("Готовый Markdown/HTML", "build:raw")],
            [button("Назад к блокам", "menu:add"), button("Главное меню", "back")],
        ]
    )


def send_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    silent = "ДА" if data.get("silent", False) else "НЕТ"
    protected = "ДА" if data.get("protected", False) else "НЕТ"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(f"Тихая отправка: {silent}", "send:toggle_silent")],
            [button(f"Защита контента: {protected}", "send:toggle_protected")],
            [button("Выбрать чат и отправить", "send:destination")],
            [button("Назад", "back")],
        ]
    )


PROMPTS = {
    "heading": "Пришлите: УРОВЕНЬ|ТЕКСТ\nПример: 2|Новости проекта",
    "paragraph": (
        "Пришлите абзац в Rich Markdown. Можно использовать:\n"
        "**жирный**, *курсив*, <u>подчеркнутый</u>, ~~зачеркнутый~~,\n"
        "||спойлер||, `код`, ==выделенный==, <sup>верх</sup>, <sub>низ</sub>,\n"
        "[ссылка](https://t.me/), $x^2 + y^2$."
    ),
    "footer": "Пришлите текст подвала.",
    "list_ul": "Пришлите пункты маркированного списка: каждый пункт с новой строки.",
    "list_ol": "Пришлите пункты нумерованного списка: каждый пункт с новой строки.",
    "list_check": (
        "Пришлите задачи с новой строки. Начните готовую задачу с +, остальные с -.\n"
        "Пример:\n+ Готово\n- Еще не готово"
    ),
    "blockquote": "Первая строка: текст цитаты.\nВторая строка (необязательно): автор.",
    "pullquote": "Первая строка: текст pull-цитаты.\nВторая строка (необязательно): автор.",
    "code": "Первая строка: язык (например python).\nОстальные строки: сам код.",
    "details": (
        "Первая строка: OPEN или CLOSED.\nВторая строка: заголовок.\n"
        "Остальные строки: содержимое Rich Markdown."
    ),
    "photo": "Пришлите: HTTPS_URL|подпись|спойлер да/нет",
    "video": "Пришлите: HTTPS_URL|подпись|спойлер да/нет",
    "audio": "Пришлите: HTTPS_URL|подпись\nСсылка должна вести прямо на аудиофайл (например .mp3).",
    "animation": "Пришлите: HTTPS_URL|подпись|спойлер да/нет\nСсылка должна вести прямо на GIF.",
    "voice": "Пришлите: HTTPS_URL|подпись\nДля голосовой заметки обычно нужен прямой URL на .ogg.",
    "collage": (
        "Пришлите прямые HTTPS-ссылки на фото/видео: каждая с новой строки.\n"
        "Последней строкой можно добавить CAPTION:Подпись"
    ),
    "slideshow": (
        "Пришлите прямые HTTPS-ссылки на фото/видео: каждая с новой строки.\n"
        "Последней строкой можно добавить CAPTION:Подпись"
    ),
    "table": (
        "Пришлите таблицу: каждая строка с новой строки, ячейки через |.\n"
        "Первая строка станет заголовками.\nПример:\nНазвание|Цена\nЧай|100\nКофе|150"
    ),
    "math": "Пришлите формулу LaTeX без знаков $$.\nПример: E = mc^2",
    "map": "Пришлите: ШИРОТА|ДОЛГОТА|МАСШТАБ\nПример: 41.9|12.5|14 (масштаб 13-20)",
    "footnote": "Пришлите: ID|текст ссылки|определение\nПример: note1|источник|Официальная документация Telegram",
    "link": (
        "Пришлите: ТИП|ТЕКСТ|ЗНАЧЕНИЕ\nТипы: url, email, phone, mention, anchor.\n"
        "Пример: mention|Иван|123456789"
    ),
    "anchor": "Пришлите имя якоря латиницей. Пример: chapter-1",
    "emoji": "Пришлите: EMOJI_ID|обычный эмодзи\nПример: 5368324170671202286|👍",
    "time": "Пришлите: UNIX_TIME|ФОРМАТ|ТЕКСТ\nПример: 1647531900|wDT|22:45 завтра",
    "raw": (
        "Пришлите готовый Rich Markdown или HTML. Он будет добавлен без изменений.\n"
        "Этот пункт нужен для rowspan/colspan, сложных вложений и других точных настроек API."
    ),
}


def get_blocks(data: dict[str, Any]) -> list[str]:
    """Безопасно получить список блоков из данных FSM."""
    return list(data.get("blocks", []))


async def append_block(state: FSMContext, block: str) -> None:
    """Добавить один готовый блок Rich Markdown к текущему посту."""
    data = await state.get_data()
    blocks = get_blocks(data)
    blocks.append(block.strip())
    await state.update_data(blocks=blocks)


def public_url(value: str) -> str:
    """Проверить, что медиа доступно Telegram по публичному URL."""
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Ссылка должна начинаться с http:// или https://")
    return url


def split_pipe(text: str, minimum: int) -> list[str]:
    parts = [part.strip() for part in text.split("|")]
    if len(parts) < minimum:
        raise ValueError("Недостаточно частей, разделенных символом |")
    return parts


def yes(value: str) -> bool:
    return value.strip().lower() in {"да", "yes", "true", "1", "+"}


def media_html(kind: str, text: str) -> str:
    """Построить отдельный HTML-медиаблок с подписью."""
    parts = [part.strip() for part in text.split("|", 2)]
    url = public_url(parts[0])
    caption = parts[1] if len(parts) > 1 else ""
    spoiler = yes(parts[2]) if len(parts) > 2 else False

    safe_url = html.escape(url, quote=True)
    safe_caption = html.escape(caption)
    spoiler_attr = " tg-spoiler" if spoiler and kind in {"photo", "video", "animation"} else ""

    if kind == "photo":
        media = f'<img src="{safe_url}"{spoiler_attr}/>'
    elif kind in {"audio", "voice"}:
        media = f'<audio src="{safe_url}"></audio>'
    else:
        media = f'<video src="{safe_url}"{spoiler_attr}></video>'

    if not caption:
        return media
    return f"<figure>{media}<figcaption>{safe_caption}</figcaption></figure>"


def gallery_html(kind: str, text: str) -> str:
    """Построить tg-collage или tg-slideshow из списка URL."""
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not raw_lines:
        raise ValueError("Нужна хотя бы одна ссылка")

    caption = ""
    if raw_lines[-1].lower().startswith("caption:"):
        caption = raw_lines.pop().split(":", 1)[1].strip()

    if not 1 <= len(raw_lines) <= 50:
        raise ValueError("Допустимо от 1 до 50 медиафайлов")

    media_tags = []
    image_extensions = (".jpg", ".jpeg", ".png", ".webp")
    for raw_url in raw_lines:
        url = public_url(raw_url)
        clean_path = url.lower().split("?", 1)[0]
        safe_url = html.escape(url, quote=True)
        if clean_path.endswith(image_extensions):
            media_tags.append(f'<img src="{safe_url}"/>')
        else:
            media_tags.append(f'<video src="{safe_url}"></video>')

    tag = "tg-collage" if kind == "collage" else "tg-slideshow"
    caption_html = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
    return f"<{tag}>{''.join(media_tags)}{caption_html}</{tag}>"


def build_block(kind: str, text: str) -> str:
    """Преобразовать ответ пользователя в официальный Rich Markdown/HTML."""
    text = text.strip()
    if not text:
        raise ValueError("Пустой блок добавить нельзя")

    if kind == "heading":
        level_text, title = split_pipe(text, 2)[:2]
        level = int(level_text)
        if level not in range(1, 7):
            raise ValueError("Уровень заголовка должен быть от 1 до 6")
        return f"{'#' * level} {title}"

    if kind in {"paragraph", "raw"}:
        return text

    if kind == "footer":
        return f"<footer>{html.escape(text)}</footer>"

    if kind in {"list_ul", "list_ol", "list_check"}:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if kind == "list_ul":
            return "\n".join(f"- {line}" for line in lines)
        if kind == "list_ol":
            return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))
        result = []
        for line in lines:
            checked = line.startswith("+")
            content = line[1:].strip() if line[:1] in {"+", "-"} else line
            result.append(f"- [{'x' if checked else ' '}] {content}")
        return "\n".join(result)

    if kind == "blockquote":
        lines = text.splitlines()
        quote = lines[0]
        author = lines[1].strip() if len(lines) > 1 else ""
        result = "\n".join(f"> {line}" for line in quote.splitlines())
        if author:
            result += f"\n>\n> {author}"
        return result

    if kind == "pullquote":
        lines = text.splitlines()
        quote = html.escape(lines[0])
        author = html.escape(lines[1].strip()) if len(lines) > 1 else ""
        return f"<aside>{quote}{f'<cite>{author}</cite>' if author else ''}</aside>"

    if kind == "code":
        lines = text.splitlines()
        if len(lines) < 2:
            raise ValueError("Укажите язык первой строкой, а код - со второй")
        language = re.sub(r"[^a-zA-Z0-9_+.-]", "", lines[0])
        code_text = "\n".join(lines[1:])
        return f"```{language}\n{code_text}\n```"

    if kind == "details":
        lines = text.splitlines()
        if len(lines) < 3:
            raise ValueError("Нужны режим, заголовок и содержимое на отдельных строках")
        open_attr = " open" if lines[0].strip().upper() == "OPEN" else ""
        summary = lines[1].strip()
        body = "\n".join(lines[2:])
        return f"<details{open_attr}><summary>{summary}</summary>\n\n{body}\n\n</details>"

    if kind in {"photo", "video", "audio", "animation", "voice"}:
        return media_html(kind, text)

    if kind in {"collage", "slideshow"}:
        return gallery_html(kind, text)

    if kind == "table":
        rows = [[cell.strip() for cell in line.split("|")] for line in text.splitlines() if line.strip()]
        if len(rows) < 2:
            raise ValueError("Нужно минимум две строки: заголовки и одна строка данных")
        columns = len(rows[0])
        if not 1 <= columns <= 20 or any(len(row) != columns for row in rows):
            raise ValueError("Во всех строках должно быть одинаковое число ячеек (максимум 20)")
        header = "| " + " | ".join(rows[0]) + " |"
        alignment = "| " + " | ".join(":---" for _ in range(columns)) + " |"
        body = ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return "\n".join([header, alignment, *body])

    if kind == "math":
        return f"$$\n{text}\n$$"

    if kind == "map":
        lat_text, long_text, zoom_text = split_pipe(text, 3)[:3]
        lat, long = float(lat_text), float(long_text)
        zoom = int(zoom_text)
        if not (-90 <= lat <= 90 and -180 <= long <= 180 and 13 <= zoom <= 20):
            raise ValueError("Проверьте координаты; масштаб должен быть от 13 до 20")
        return f'<tg-map lat="{lat}" long="{long}" zoom="{zoom}"/>'

    if kind == "footnote":
        note_id, label, definition = split_pipe(text, 3)[:3]
        note_id = re.sub(r"[^a-zA-Z0-9_-]", "", note_id)
        if not note_id:
            raise ValueError("ID сноски должен содержать латинские буквы или цифры")
        return f"{label}[^{note_id}]\n\n[^{note_id}]: {definition}"

    if kind == "link":
        link_type, label, target = split_pipe(text, 3)[:3]
        prefixes = {
            "url": "",
            "email": "mailto:",
            "phone": "tel:",
            "mention": "tg://user?id=",
            "anchor": "#",
        }
        link_type = link_type.lower()
        if link_type not in prefixes:
            raise ValueError("Тип должен быть: url, email, phone, mention или anchor")
        return f"[{label}]({prefixes[link_type]}{target})"

    if kind == "anchor":
        name = re.sub(r"[^a-zA-Z0-9_-]", "", text)
        if not name:
            raise ValueError("Имя якоря должно содержать латинские буквы или цифры")
        return f'<a name="{name}"></a>'

    if kind == "emoji":
        emoji_id, alternative = split_pipe(text, 2)[:2]
        if not emoji_id.isdigit():
            raise ValueError("ID кастомного эмодзи должен состоять из цифр")
        return f"![{alternative}](tg://emoji?id={emoji_id})"

    if kind == "time":
        unix_text, time_format, label = split_pipe(text, 3)[:3]
        unix_time = int(unix_text)
        return f"![{label}](tg://time?unix={unix_time}&format={time_format})"

    raise ValueError(f"Неизвестный тип блока: {kind}")


def assembled_markdown(data: dict[str, Any]) -> str:
    blocks = get_blocks(data)
    markdown = "\n\n".join(blocks)
    if not markdown:
        raise ValueError("Пост пуст. Сначала добавьте хотя бы один блок.")
    if len(markdown.encode("utf-8")) > 32768:
        raise ValueError("Пост превышает лимит 32768 байт UTF-8")
    return markdown


async def show_main(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    count = len(get_blocks(data))
    await target.answer(f"Конструктор Rich-постов. Блоков в посте: {count}", reply_markup=main_menu())


@dp.message(Command("start"))
async def command_start(message: types.Message, state: FSMContext) -> None:
    await show_main(message, state)


@dp.message(Command("help"))
async def command_help(message: types.Message) -> None:
    await message.answer(
        "Собирайте пост через кнопку 'Добавить блок'.\n\n"
        "Медиа: нужна публичная прямая HTTP/HTTPS-ссылка на файл. Просто загруженный в чат "
        "файл нельзя вставить в Rich Markdown без внешнего URL.\n\n"
        "Команды:\n/start - главное меню\n/cancel - отменить ввод\n"
        "/edit CHAT_ID MESSAGE_ID - заменить отправленный пост текущим черновиком"
    )


@dp.message(Command("cancel"))
async def command_cancel(message: types.Message, state: FSMContext) -> None:
    await state.set_state(None)
    await message.answer("Текущий ввод отменен. Собранные блоки сохранены в памяти.", reply_markup=main_menu())


@dp.message(Command("edit"))
async def command_edit(message: types.Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: /edit CHAT_ID MESSAGE_ID")
        return
    raw_chat_id, raw_message_id = parts[1], parts[2]
    chat_id: int | str = int(raw_chat_id) if raw_chat_id.lstrip("-").isdigit() else raw_chat_id
    try:
        message_id = int(raw_message_id)
        markdown = assembled_markdown(await state.get_data())
        await edit_rich_message(chat_id, message_id, markdown=markdown)
    except (ValueError, TelegramAPIError) as error:
        await message.answer(f"Не удалось отредактировать пост:\n{error}")
        return
    await message.answer("Пост отредактирован.", reply_markup=main_menu())


@dp.callback_query(F.data == "back")
async def callback_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    data = await state.get_data()
    count = len(get_blocks(data))
    await callback.message.edit_text(f"Конструктор Rich-постов. Блоков: {count}", reply_markup=main_menu())
    await callback.answer()


@dp.callback_query(F.data == "menu:add")
async def callback_add_menu(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text("Выберите тип блока:", reply_markup=add_text_menu())
    await callback.answer()


@dp.callback_query(F.data == "menu:media")
async def callback_media_menu(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите медиа. Нужна публичная прямая HTTP/HTTPS-ссылка на файл:",
        reply_markup=media_menu(),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu:advanced")
async def callback_advanced_menu(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text("Дополнительные Rich-блоки:", reply_markup=advanced_menu())
    await callback.answer()


@dp.callback_query(F.data.startswith("build:"))
async def callback_start_block(callback: types.CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":", 1)[1]
    prompt = PROMPTS.get(kind)
    if not prompt:
        await callback.answer("Неизвестный тип блока", show_alert=True)
        return
    await state.update_data(pending=kind)
    await state.set_state(Builder.waiting_block)
    await callback.message.answer(prompt + "\n\nДля отмены: /cancel")
    await callback.answer()


@dp.callback_query(F.data == "instant:divider")
async def callback_divider(callback: types.CallbackQuery, state: FSMContext) -> None:
    await append_block(state, "---")
    await callback.answer("Разделитель добавлен")
    await callback.message.answer("Разделитель добавлен.", reply_markup=main_menu())


@dp.message(Builder.waiting_block, F.text)
async def receive_block(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("pending", "")
    try:
        block = build_block(kind, message.text)
        await append_block(state, block)
    except (ValueError, TypeError) as error:
        await message.answer(f"Ошибка: {error}\n\nПопробуйте еще раз или отправьте /cancel.")
        return

    await state.set_state(None)
    count = len(get_blocks(await state.get_data()))
    await message.answer(f"Блок добавлен. Всего блоков: {count}", reply_markup=main_menu())


@dp.message(Builder.waiting_block)
async def receive_non_text_block(message: types.Message) -> None:
    await message.answer(
        "Для этого конструктора пришлите текст или прямую HTTP/HTTPS-ссылку. "
        "Загруженный файл не имеет публичного URL. Для отмены: /cancel"
    )


@dp.callback_query(F.data == "preview")
async def callback_preview(callback: types.CallbackQuery, state: FSMContext) -> None:
    try:
        markdown = assembled_markdown(await state.get_data())
        await send_rich_message(callback.from_user.id, markdown=markdown)
    except (ValueError, TelegramAPIError) as error:
        await callback.answer("Предпросмотр не отправлен", show_alert=True)
        await callback.message.answer(f"Ошибка Telegram:\n{error}")
        return
    await callback.answer("Предпросмотр отправлен вам в личный чат")


@dp.callback_query(F.data == "menu:send")
async def callback_send_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await callback.message.edit_text("Настройки отправки:", reply_markup=send_menu(data))
    await callback.answer()


@dp.callback_query(F.data.in_({"send:toggle_silent", "send:toggle_protected"}))
async def callback_toggle_send(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    key = "silent" if callback.data.endswith("silent") else "protected"
    await state.update_data(**{key: not data.get(key, False)})
    await callback.message.edit_reply_markup(reply_markup=send_menu(await state.get_data()))
    await callback.answer()


@dp.callback_query(F.data == "send:destination")
async def callback_destination(callback: types.CallbackQuery, state: FSMContext) -> None:
    try:
        assembled_markdown(await state.get_data())
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await state.set_state(Builder.waiting_destination)
    await callback.message.answer(
        "Пришлите @username канала/группы или числовой chat_id.\n"
        "Для личной отправки можно прислать user_id. Для отмены: /cancel"
    )
    await callback.answer()


@dp.message(Builder.waiting_destination, F.text)
async def receive_destination(message: types.Message, state: FSMContext) -> None:
    raw_chat_id = message.text.strip()
    chat_id: int | str = int(raw_chat_id) if raw_chat_id.lstrip("-").isdigit() else raw_chat_id
    data = await state.get_data()
    try:
        markdown = assembled_markdown(data)
        sent = await send_rich_message(
            chat_id,
            markdown=markdown,
            disable_notification=data.get("silent", False),
            protect_content=data.get("protected", False),
        )
        await log_sent(chat_id, sent["message_id"], "Rich post")
    except (ValueError, TelegramAPIError) as error:
        await message.answer(f"Не удалось отправить пост:\n{error}\n\nПришлите другой chat_id или /cancel.")
        return

    await state.set_state(None)
    await message.answer(
        f"Пост отправлен. chat_id={chat_id}, message_id={sent['message_id']}",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "draft:save")
async def callback_save_draft(callback: types.CallbackQuery, state: FSMContext) -> None:
    try:
        markdown = assembled_markdown(await state.get_data())
        await save_draft(callback.from_user.id, markdown, "")
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer("Черновик сохранен в SQLite", show_alert=True)


@dp.callback_query(F.data == "draft:load")
async def callback_load_draft(callback: types.CallbackQuery, state: FSMContext) -> None:
    row = await load_draft(callback.from_user.id)
    if not row or not row[0]:
        await callback.answer("Сохраненного черновика нет", show_alert=True)
        return
    await state.update_data(blocks=[row[0]])
    await callback.answer("Черновик загружен", show_alert=True)
    await callback.message.answer("Черновик загружен.", reply_markup=main_menu())


@dp.callback_query(F.data == "menu:templates")
async def callback_templates(callback: types.CallbackQuery, state: FSMContext) -> None:
    names = await list_templates()
    await state.update_data(template_names=names)
    rows = [[button("Сохранить текущий как шаблон", "template:save")]]
    rows.extend([[button(f"Загрузить: {name[:35]}", f"template:load:{index}")] for index, name in enumerate(names)])
    rows.append([button("Назад", "back")])
    await callback.message.edit_text(
        "Шаблоны:" if names else "Шаблонов пока нет.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data == "template:save")
async def callback_template_name(callback: types.CallbackQuery, state: FSMContext) -> None:
    try:
        assembled_markdown(await state.get_data())
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await state.set_state(Builder.waiting_template_name)
    await callback.message.answer("Пришлите название шаблона (до 50 символов). Для отмены: /cancel")
    await callback.answer()


@dp.message(Builder.waiting_template_name, F.text)
async def receive_template_name(message: types.Message, state: FSMContext) -> None:
    name = message.text.strip()[:50]
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    markdown = assembled_markdown(await state.get_data())
    await save_template(name, markdown, "")
    await state.set_state(None)
    await message.answer(f"Шаблон '{name}' сохранен.", reply_markup=main_menu())


@dp.callback_query(F.data.startswith("template:load:"))
async def callback_load_template(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    names = data.get("template_names", [])
    try:
        name = names[int(callback.data.rsplit(":", 1)[1])]
    except (IndexError, ValueError):
        await callback.answer("Список шаблонов устарел. Откройте меню заново.", show_alert=True)
        return
    row = await load_template(name)
    if not row:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await state.update_data(blocks=[row[0]])
    await callback.answer("Шаблон загружен", show_alert=True)
    await callback.message.answer(f"Шаблон '{name}' загружен.", reply_markup=main_menu())


@dp.callback_query(F.data == "undo")
async def callback_undo(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    blocks = get_blocks(data)
    if not blocks:
        await callback.answer("Пост уже пуст", show_alert=True)
        return
    blocks.pop()
    await state.update_data(blocks=blocks)
    await callback.answer("Последний блок удален")
    await callback.message.answer(f"Осталось блоков: {len(blocks)}", reply_markup=main_menu())


@dp.callback_query(F.data == "reset")
async def callback_reset(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Конструктор очищен", show_alert=True)
    await callback.message.edit_text("Конструктор очищен. Блоков: 0", reply_markup=main_menu())


@dp.callback_query(F.data == "help")
async def callback_help(callback: types.CallbackQuery) -> None:
    await callback.message.answer(
        "Выберите 'Добавить блок'. Меню разделено на текст, медиа и дополнительные блоки.\n"
        "Медиа принимаются только по прямым HTTP/HTTPS-ссылкам, как требует Rich Message API."
    )
    await callback.answer()


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Проверьте файл .env или Variables в Railway.")
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
