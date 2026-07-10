"""
main.py - полный чат-конструктор Rich-постов.

- aiogram 3 для кнопок, команд и FSM;
- sendRichMessage вызывается напрямую через httpx (см. rich_sender.py);
- при приёме файлов бот автоматически льёт их на публичный файлохостинг и
  подставляет HTTPS-ссылку в post (загружать файл не обязательно, можно сразу
  прислать URL);
- все тексты вынесены в strings.py; там их удобно править.
"""
import asyncio
import html as html_mod
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
from uploader import upload_user_media
import strings as S


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


class Builder(StatesGroup):
    waiting_block = State()
    waiting_destination = State()
    waiting_template_name = State()
    waiting_gallery = State()  # сбор коллажа/слайдшоу по файлам


# ─────────────── Авторизация ───────────────
class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user and ALLOWED_USER_IDS and user.id not in ALLOWED_USER_IDS:
            if isinstance(event, types.CallbackQuery):
                await event.answer("Доступ запрещён", show_alert=True)
            elif isinstance(event, types.Message):
                await event.answer("Доступ к этому боту запрещён.")
            return None
        return await handler(event, data)


dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())


# ─────────────── Клавиатуры ───────────────
def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def main_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    return kb([
        [btn(S.BTN_ADD, "menu:add")],
        [btn(S.BTN_PREVIEW, "preview"), btn(S.BTN_SEND, "menu:send")],
        [btn(S.BTN_SAVE_DRAFT, "draft:save"), btn(S.BTN_LOAD_DRAFT, "draft:load")],
        [btn(S.BTN_TEMPLATES, "menu:templates"), btn(S.BTN_UNDO, "undo")],
        [btn(S.BTN_RESET, "reset"), btn(S.BTN_HELP, "help")],
    ])


def text_menu() -> InlineKeyboardMarkup:
    return kb([
        [btn(S.BTN_HEADING, "build:heading"), btn(S.BTN_PARAGRAPH, "build:paragraph")],
        [btn(S.BTN_DIVIDER, "instant:divider"), btn(S.BTN_FOOTER, "build:footer")],
        [btn(S.BTN_LIST_UL, "build:list_ul")],
        [btn(S.BTN_LIST_OL, "build:list_ol")],
        [btn(S.BTN_LIST_CHECK, "build:list_check")],
        [btn(S.BTN_QUOTE, "build:blockquote"), btn(S.BTN_PULLQUOTE, "build:pullquote")],
        [btn(S.BTN_CODE, "build:code"), btn(S.BTN_DETAILS, "build:details")],
        [btn(S.BTN_TO_MEDIA, "menu:media"), btn(S.BTN_TO_ADVANCED, "menu:advanced")],
        [btn(S.BTN_BACK, "back")],
    ])


def media_menu() -> InlineKeyboardMarkup:
    return kb([
        [btn(S.BTN_PHOTO, "build:photo"), btn(S.BTN_VIDEO, "build:video")],
        [btn(S.BTN_AUDIO, "build:audio"), btn(S.BTN_ANIM, "build:animation")],
        [btn(S.BTN_VOICE, "build:voice")],
        [btn(S.BTN_COLLAGE, "build:collage"), btn(S.BTN_SLIDESHOW, "build:slideshow")],
        [btn(S.BTN_TO_TEXT, "menu:add"), btn(S.BTN_TO_ADVANCED, "menu:advanced")],
        [btn(S.BTN_BACK, "back")],
    ])


def advanced_menu() -> InlineKeyboardMarkup:
    return kb([
        [btn(S.BTN_TABLE, "build:table"), btn(S.BTN_MATH, "build:math")],
        [btn(S.BTN_MAP, "build:map"), btn(S.BTN_FOOTNOTE, "build:footnote")],
        [btn(S.BTN_LINK, "build:link"), btn(S.BTN_ANCHOR, "build:anchor")],
        [btn(S.BTN_EMOJI, "build:emoji"), btn(S.BTN_TIME, "build:time")],
        [btn(S.BTN_RAW, "build:raw")],
        [btn(S.BTN_TO_TEXT, "menu:add"), btn(S.BTN_TO_MEDIA, "menu:media")],
        [btn(S.BTN_BACK, "back")],
    ])


def send_menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    return kb([
        [btn(S.BTN_SILENT.format(state="✅" if data.get("silent") else "⬜"), "send:toggle_silent")],
        [btn(S.BTN_PROTECTED.format(state="✅" if data.get("protected") else "⬜"), "send:toggle_protected")],
        [btn(S.BTN_DESTINATION, "send:destination")],
        [btn(S.BTN_BACK, "back")],
    ])


# ─────────────── Хелперы блоков ───────────────
def get_blocks(data: dict[str, Any]) -> list[str]:
    return list(data.get("blocks", []))


async def append_block(state: FSMContext, block_md: str) -> int:
    data = await state.get_data()
    blocks = get_blocks(data)
    blocks.append(block_md.strip())
    await state.update_data(blocks=blocks)
    return len(blocks)


def require_public_url(value: str) -> str:
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Ссылка должна начинаться с http:// или https://")
    return url


def split_pipe(text: str, minimum: int) -> list[str]:
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < minimum:
        raise ValueError("Недостаточно частей, разделённых |")
    return parts


def yes(value: str) -> bool:
    return value.strip().lower() in {"да", "yes", "true", "1", "+"}


def media_html(kind: str, text: str) -> str:
    parts = [p.strip() for p in text.split("|", 2)]
    url = require_public_url(parts[0])
    caption = parts[1] if len(parts) > 1 else ""
    spoiler = yes(parts[2]) if len(parts) > 2 else False
    safe_url = html_mod.escape(url, quote=True)
    safe_caption = html_mod.escape(caption)
    spoiler_attr = ' tg-spoiler' if spoiler and kind in {"photo", "video", "animation"} else ""
    if kind == "photo":
        tag = f'<img src="{safe_url}"{spoiler_attr}/>'
    elif kind in {"audio", "voice"}:
        tag = f'<audio src="{safe_url}"></audio>'
    else:
        tag = f'<video src="{safe_url}"{spoiler_attr}></video>'
    if not caption:
        return tag
    return f"<figure>{tag}<figcaption>{safe_caption}</figcaption></figure>"


def gallery_html(kind: str, entries: list[tuple[str, str | None]]) -> str:
    if not 1 <= len(entries) <= 50:
        raise ValueError("Допустимо от 1 до 50 медиа")
    tags = []
    caption = ""
    img_ext = (".jpg", ".jpeg", ".png", ".webp")
    for url, _ in entries:
        path = url.lower().split("?", 1)[0]
        safe = html_mod.escape(url, quote=True)
        tags.append(f'<img src="{safe}"/>' if path.endswith(img_ext) else f'<video src="{safe}"></video>')
    # последний caption берём из записей с пометкой caption
    for _, cap in entries:
        if cap:
            caption = cap
    tag = "tg-collage" if kind == "collage" else "tg-slideshow"
    cap_html = f"<figcaption>{html_mod.escape(caption)}</figcaption>" if caption else ""
    return f"<{tag}>{''.join(tags)}{cap_html}</{tag}>"


def build_block(kind: str, text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Пустой блок добавить нельзя")

    if kind == "heading":
        lvl, title = split_pipe(text, 2)[:2]
        n = int(lvl)
        if not 1 <= n <= 6:
            raise ValueError("Уровень заголовка 1..6")
        return f"{'#' * n} {title}"
    if kind in {"paragraph", "raw"}:
        return text
    if kind == "footer":
        return f"<footer>{html_mod.escape(text)}</footer>"
    if kind in {"list_ul", "list_ol", "list_check"}:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if kind == "list_ul":
            return "\n".join(f"- {ln}" for ln in lines)
        if kind == "list_ol":
            return "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))
        out = []
        for ln in lines:
            done = ln.startswith("+")
            body = ln[1:].strip() if ln[:1] in "+-" else ln
            out.append(f"- [{'x' if done else ' '}] {body}")
        return "\n".join(out)
    if kind == "blockquote":
        lines = text.splitlines()
        quote = lines[0]
        author = lines[1].strip() if len(lines) > 1 else ""
        out = "\n".join(f"> {ln}" for ln in quote.splitlines())
        if author:
            out += f"\n>\n> {author}"
        return out
    if kind == "pullquote":
        lines = text.splitlines()
        body = html_mod.escape(lines[0])
        author = html_mod.escape(lines[1].strip()) if len(lines) > 1 else ""
        return f"<aside>{body}{f'<cite>{author}</cite>' if author else ''}</aside>"
    if kind == "code":
        lines = text.splitlines()
        if len(lines) < 2:
            raise ValueError("Первая строка - язык, далее код")
        lang = re.sub(r"[^a-zA-Z0-9_+.-]", "", lines[0])
        body = "\n".join(lines[1:])
        return f"```{lang}\n{body}\n```"
    if kind == "details":
        lines = text.splitlines()
        if len(lines) < 3:
            raise ValueError("Нужны: OPEN/CLOSED, заголовок, содержимое")
        open_attr = " open" if lines[0].strip().upper() == "OPEN" else ""
        summary = lines[1].strip()
        body = "\n".join(lines[2:])
        return f"<details{open_attr}><summary>{summary}</summary>\n\n{body}\n\n</details>"
    if kind in {"photo", "video", "audio", "animation", "voice"}:
        return media_html(kind, text)
    if kind == "table":
        rows = [[c.strip() for c in ln.split("|")] for ln in text.splitlines() if ln.strip()]
        if len(rows) < 2:
            raise ValueError("Нужен хотя бы заголовок и одна строка")
        cols = len(rows[0])
        if not 1 <= cols <= 20 or any(len(r) != cols for r in rows):
            raise ValueError("Число ячеек должно быть одинаковым и ≤ 20")
        head = "| " + " | ".join(rows[0]) + " |"
        align = "| " + " | ".join(":---" for _ in range(cols)) + " |"
        body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join([head, align, *body])
    if kind == "math":
        return f"$$\n{text}\n$$"
    if kind == "map":
        lat, lon, z = split_pipe(text, 3)[:3]
        lat_f, lon_f, z_i = float(lat), float(lon), int(z)
        if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180 and 13 <= z_i <= 20):
            raise ValueError("Координаты или масштаб некорректны (zoom 13..20)")
        return f'<tg-map lat="{lat_f}" long="{lon_f}" zoom="{z_i}"/>'
    if kind == "footnote":
        fid, label, defn = split_pipe(text, 3)[:3]
        fid = re.sub(r"[^a-zA-Z0-9_-]", "", fid)
        if not fid:
            raise ValueError("ID сноски должен содержать латиницу/цифры")
        return f"{label}[^{fid}]\n\n[^{fid}]: {defn}"
    if kind == "link":
        t, lbl, tgt = split_pipe(text, 3)[:3]
        prefixes = {"url": "", "email": "mailto:", "phone": "tel:", "mention": "tg://user?id=", "anchor": "#"}
        t = t.lower()
        if t not in prefixes:
            raise ValueError("Тип: url, email, phone, mention, anchor")
        return f"[{lbl}]({prefixes[t]}{tgt})"
    if kind == "anchor":
        name = re.sub(r"[^a-zA-Z0-9_-]", "", text)
        if not name:
            raise ValueError("Имя якоря должно содержать латиницу/цифры")
        return f'<a name="{name}"></a>'
    if kind == "emoji":
        eid, alt = split_pipe(text, 2)[:2]
        if not eid.isdigit():
            raise ValueError("ID кастомного эмодзи должен быть числом")
        return f"![{alt}](tg://emoji?id={eid})"
    if kind == "time":
        u, fmt, lbl = split_pipe(text, 3)[:3]
        return f"![{lbl}](tg://time?unix={int(u)}&format={fmt})"
    raise ValueError(f"Неизвестный тип: {kind}")


def assembled_markdown(data: dict[str, Any]) -> str:
    blocks = get_blocks(data)
    if not blocks:
        raise ValueError(S.EMPTY_POST)
    md = "\n\n".join(blocks)
    if len(md.encode("utf-8")) > 32768:
        raise ValueError(S.POST_TOO_BIG)
    return md


async def show_main(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    count = len(get_blocks(data))
    await target.answer(
        S.MAIN_TITLE.format(count=count),
        reply_markup=main_menu(data),
    )


# ─────────────── Команды ───────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await message.answer(S.WELCOME)
    await show_main(message, state)


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(S.HELP_TEXT)


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    await state.set_state(None)
    await message.answer(S.CANCELLED, reply_markup=main_menu(await state.get_data()))


@dp.message(Command("done"))
async def cmd_done(message: types.Message, state: FSMContext) -> None:
    """Завершить сбор коллажа/слайдшоу."""
    data = await state.get_data()
    if data.get("pending") not in {"collage", "slideshow"}:
        return
    entries = data.get("gallery_entries", [])
    kind = data["pending"]
    try:
        block = gallery_html(kind, entries)
        n = await append_block(state, block)
    except ValueError as e:
        await message.answer(f"⚠️ {e}")
        return
    await state.update_data(pending=None, gallery_entries=None)
    await state.set_state(None)
    await message.answer(S.BLOCK_ADDED.format(count=n), reply_markup=main_menu(await state.get_data()))


@dp.message(Command("edit"))
async def cmd_edit(message: types.Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(S.EDIT_PROMPT_FORMAT)
        return
    raw_chat, raw_mid = parts[1], parts[2]
    chat_id: int | str = int(raw_chat) if raw_chat.lstrip("-").isdigit() else raw_chat
    try:
        mid = int(raw_mid)
        md = assembled_markdown(await state.get_data())
        await edit_rich_message(chat_id, mid, markdown=md)
    except (ValueError, TelegramAPIError) as e:
        await message.answer(S.EDIT_ERROR.format(error=e))
        return
    await message.answer(S.EDIT_OK, reply_markup=main_menu(await state.get_data()))


# ─────────────── Callback-обработчики ───────────────
@dp.callback_query(F.data == "back")
async def cb_back(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    data = await state.get_data()
    await cb.message.edit_text(S.MAIN_TITLE.format(count=len(get_blocks(data))), reply_markup=main_menu(data))
    await cb.answer()


@dp.callback_query(F.data == "menu:add")
async def cb_text(cb: types.CallbackQuery) -> None:
    await cb.message.edit_text(S.MENU_TEXT_TITLE, reply_markup=text_menu())
    await cb.answer()


@dp.callback_query(F.data == "menu:media")
async def cb_media(cb: types.CallbackQuery) -> None:
    await cb.message.edit_text(S.MENU_MEDIA_TITLE, reply_markup=media_menu())
    await cb.answer()


@dp.callback_query(F.data == "menu:advanced")
async def cb_adv(cb: types.CallbackQuery) -> None:
    await cb.message.edit_text(S.MENU_ADVANCED_TITLE, reply_markup=advanced_menu())
    await cb.answer()


@dp.callback_query(F.data == "instant:divider")
async def cb_divider(cb: types.CallbackQuery, state: FSMContext) -> None:
    n = await append_block(state, "---")
    await cb.answer()
    await cb.message.answer(S.BLOCK_ADDED_INSTANT.format(kind="разделитель"), reply_markup=main_menu(await state.get_data()))


@dp.callback_query(F.data.startswith("build:"))
async def cb_start_build(cb: types.CallbackQuery, state: FSMContext) -> None:
    kind = cb.data.split(":", 1)[1]
    await state.update_data(pending=kind, gallery_entries=[] if kind in {"collage", "slideshow"} else None)
    if kind in {"collage", "slideshow"}:
        await state.set_state(Builder.waiting_gallery)
        await cb.message.answer(S.PROMPTS[kind] + S.WAITING_CANCEL_HINT)
    else:
        await state.set_state(Builder.waiting_block)
        await cb.message.answer(S.PROMPTS[kind] + S.WAITING_CANCEL_HINT)
    await cb.answer()


@dp.callback_query(F.data == "preview")
async def cb_preview(cb: types.CallbackQuery, state: FSMContext) -> None:
    try:
        md = assembled_markdown(await state.get_data())
        await send_rich_message(cb.from_user.id, markdown=md)
    except (ValueError, TelegramAPIError) as e:
        await cb.answer("Превью не отправлено", show_alert=True)
        await cb.message.answer(f"⚠️ {e}")
        return
    await cb.answer("Превью отправлено в чат")


@dp.callback_query(F.data == "menu:send")
async def cb_send(cb: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await cb.message.edit_text(S.SEND_TITLE, reply_markup=send_menu(data))
    await cb.answer()


@dp.callback_query(F.data.in_({"send:toggle_silent", "send:toggle_protected"}))
async def cb_toggle(cb: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    key = "silent" if cb.data.endswith("silent") else "protected"
    await state.update_data(**{key: not data.get(key, False)})
    await cb.message.edit_reply_markup(reply_markup=send_menu(await state.get_data()))
    await cb.answer()


@dp.callback_query(F.data == "send:destination")
async def cb_dest(cb: types.CallbackQuery, state: FSMContext) -> None:
    try:
        assembled_markdown(await state.get_data())
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await state.set_state(Builder.waiting_destination)
    await cb.message.answer(S.SEND_PROMPT + S.WAITING_CANCEL_HINT)
    await cb.answer()


@dp.callback_query(F.data == "draft:save")
async def cb_dsave(cb: types.CallbackQuery, state: FSMContext) -> None:
    try:
        md = assembled_markdown(await state.get_data())
        await save_draft(cb.from_user.id, md, "")
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await cb.answer(S.DRAFT_SAVED, show_alert=True)


@dp.callback_query(F.data == "draft:load")
async def cb_dload(cb: types.CallbackQuery, state: FSMContext) -> None:
    row = await load_draft(cb.from_user.id)
    if not row or not row[0]:
        await cb.answer(S.DRAFT_NOT_FOUND, show_alert=True)
        return
    # При загрузке восстанавливаем черновик как единый "raw"-блок, чтобы можно было редактировать.
    await state.update_data(blocks=[row[0]])
    await cb.answer(S.DRAFT_LOADED, show_alert=True)
    await cb.message.answer(S.DRAFT_LOADED, reply_markup=main_menu(await state.get_data()))


@dp.callback_query(F.data == "menu:templates")
async def cb_templates(cb: types.CallbackQuery, state: FSMContext) -> None:
    names = await list_templates()
    await state.update_data(template_names=names)
    rows = [[btn(S.BTN_SAVE_AS_TEMPLATE, "template:save")]]
    for i, n in enumerate(names):
        rows.append([btn(f"{S.TEMPLATE_LOAD_PREFIX}{n[:35]}", f"template:load:{i}")])
    rows.append([btn(S.BTN_BACK, "back")])
    await cb.message.edit_text(S.TEMPLATES_EMPTY if not names else "📂 <b>Шаблоны:</b>", reply_markup=kb(rows))
    await cb.answer()


@dp.callback_query(F.data == "template:save")
async def cb_tpl_save(cb: types.CallbackQuery, state: FSMContext) -> None:
    try:
        assembled_markdown(await state.get_data())
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await state.set_state(Builder.waiting_template_name)
    await cb.message.answer(S.TEMPLATE_NAME_PROMPT + S.WAITING_CANCEL_HINT)
    await cb.answer()


@dp.callback_query(F.data.startswith("template:load:"))
async def cb_tpl_load(cb: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    names = data.get("template_names", [])
    try:
        name = names[int(cb.data.rsplit(":", 1)[1])]
    except (IndexError, ValueError):
        await cb.answer("Список устарел, откройте заново", show_alert=True)
        return
    row = await load_template(name)
    if not row:
        await cb.answer(S.TEMPLATE_NOT_FOUND, show_alert=True)
        return
    await state.update_data(blocks=[row[0]])
    await cb.answer(S.TEMPLATE_LOADED.format(name=name), show_alert=True)
    await cb.message.answer(S.TEMPLATE_LOADED.format(name=name), reply_markup=main_menu(await state.get_data()))


@dp.callback_query(F.data == "undo")
async def cb_undo(cb: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    blocks = get_blocks(data)
    if not blocks:
        await cb.answer(S.UNDO_EMPTY, show_alert=True)
        return
    blocks.pop()
    await state.update_data(blocks=blocks)
    await cb.message.answer(S.UNDO_OK.format(count=len(blocks)), reply_markup=main_menu(await state.get_data()))
    await cb.answer()


@dp.callback_query(F.data == "reset")
async def cb_reset(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(S.RESET_OK, reply_markup=main_menu({}))
    await cb.answer()


@dp.callback_query(F.data == "help")
async def cb_help(cb: types.CallbackQuery) -> None:
    await cb.message.answer(S.HELP_TEXT)
    await cb.answer()


# ─────────────── Приём текстовых данных для блоков ───────────────
@dp.message(Builder.waiting_block, F.text)
async def got_text_block(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("pending", "")
    try:
        block = build_block(kind, message.text)
        n = await append_block(state, block)
    except (ValueError, TypeError) as e:
        await message.answer(S.BLOCK_ERROR.format(error=e))
        return
    await state.set_state(None)
    await state.update_data(pending=None)
    await message.answer(S.BLOCK_ADDED.format(count=n), reply_markup=main_menu(await state.get_data()))


# ─────────────── Приём ФАЙЛОВ как блоков ───────────────
@dp.message(Builder.waiting_block)
async def got_file_block(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("pending", "")
    if kind not in {"photo", "video", "audio", "animation", "voice"}:
        await message.answer(S.NON_TEXT_BLOCK_ERROR)
        return

    status = await message.answer(S.MEDIA_RECEIVED)
    try:
        url, name, _mime = await upload_user_media(bot, message)
    except Exception as e:
        await status.edit_text(S.MEDIA_ERROR.format(error=e))
        return

    caption = message.caption or ""
    parts = [url, caption]
    # Пользователь может передать "да/нет" спойлера подписью к файлу через |
    if "|" in caption:
        cap, sp = caption.rsplit("|", 1)
        parts = [url, cap.strip(), sp.strip()]
    else:
        parts = [url, caption]
    block = build_block(kind, "|".join(parts))
    n = await append_block(state, block)
    await state.set_state(None)
    await state.update_data(pending=None)
    await message.answer(S.MEDIA_UPLOADED.format(url=url))
    await message.answer(S.BLOCK_ADDED.format(count=n), reply_markup=main_menu(await state.get_data()))


# ─────────────── Сбор галереи (коллаж/слайдшоу) по файлам ──────
@dp.message(Builder.waiting_gallery, F.text)
async def got_gallery_text(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("pending", "")
    text = (message.text or "").strip()
    entries: list[tuple[str, str | None]] = list(data.get("gallery_entries", []))

    # Поддержка "CAPTION: ..."
    if text.lower().startswith("caption:"):
        cap = text.split(":", 1)[1].strip()
        if entries and entries[-1][1] is None:
            entries[-1] = (entries[-1][0], cap)
        else:
            # caption без файла - добавим как глобальную подпись через пустой url
            entries.append(("", cap))
    else:
        # строки-ссылки
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in lines:
            if ln.lower().startswith("caption:"):
                cap = ln.split(":", 1)[1].strip()
                if entries:
                    entries[-1] = (entries[-1][0], cap)
                else:
                    entries.append(("", cap))
            else:
                require_public_url(ln)
                entries.append((ln, None))

    await state.update_data(gallery_entries=entries)
    await message.answer(f"✅ Добавлено ссылок: {len([e for e in entries if e[0]])}. Отправьте ещё файлы/ссылки или /done.")


@dp.message(Builder.waiting_gallery)
async def got_gallery_file(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("pending", "")
    entries: list[tuple[str, str | None]] = list(data.get("gallery_entries", []))
    status = await message.answer(S.MEDIA_RECEIVED)
    try:
        url, _name, _m = await upload_user_media(bot, message)
    except Exception as e:
        await status.edit_text(S.MEDIA_ERROR.format(error=e))
        return
    cap = message.caption
    entries.append((url, cap))
    await state.update_data(gallery_entries=entries)
    await message.answer(S.MEDIA_UPLOADED.format(url=url) + "\nОтправьте ещё или /done.")


# ─────────────── Отправка ───────────────
@dp.message(Builder.waiting_destination, F.text)
async def got_destination(message: types.Message, state: FSMContext) -> None:
    raw = message.text.strip()
    chat_id: int | str = int(raw) if raw.lstrip("-").isdigit() else raw
    data = await state.get_data()
    try:
        md = assembled_markdown(data)
        sent = await send_rich_message(
            chat_id,
            markdown=md,
            disable_notification=data.get("silent", False),
            protect_content=data.get("protected", False),
        )
        await log_sent(chat_id, sent["message_id"], "Rich post")
    except (ValueError, TelegramAPIError) as e:
        await message.answer(S.SEND_ERROR.format(error=e))
        return
    await state.set_state(None)
    await message.answer(
        S.SEND_OK.format(chat_id=chat_id, message_id=sent["message_id"]),
        reply_markup=main_menu(await state.get_data()),
    )


@dp.message(Builder.waiting_template_name, F.text)
async def got_tpl_name(message: types.Message, state: FSMContext) -> None:
    name = message.text.strip()[:50]
    if not name:
        await message.answer("Пустое имя недопустимо")
        return
    md = assembled_markdown(await state.get_data())
    await save_template(name, md, "")
    await state.set_state(None)
    await message.answer(S.TEMPLATE_SAVED.format(name=name), reply_markup=main_menu(await state.get_data()))


# ─────────────── Запуск ───────────────
async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден. Проверь .env или Variables в Railway.")
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
