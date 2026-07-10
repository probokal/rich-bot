"""
rich_sender.py - прямые вызовы новых методов Telegram Bot API.

Библиотека aiogram может еще не иметь удобных оберток над самыми новыми
методами, поэтому sendRichMessage вызывается обычным HTTPS-запросом.
"""
from typing import Any

import httpx

from config import BOT_TOKEN


class TelegramAPIError(RuntimeError):
    """Ошибка, которую вернул Telegram Bot API."""


async def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Выполнить метод Bot API и вернуть поле result."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload)
            data = response.json()
    except httpx.RequestError as error:
        raise TelegramAPIError(f"Сетевая ошибка: {error}") from error
    except ValueError as error:
        raise TelegramAPIError("Telegram вернул ответ не в формате JSON") from error

    if not data.get("ok"):
        description = data.get("description", "неизвестная ошибка")
        error_code = data.get("error_code", response.status_code)
        raise TelegramAPIError(f"Telegram API {error_code}: {description}")

    return data["result"]


async def send_rich_message(
    chat_id: int | str,
    *,
    markdown: str | None = None,
    html: str | None = None,
    disable_notification: bool = False,
    protect_content: bool = False,
    message_thread_id: int | None = None,
    skip_entity_detection: bool = False,
) -> dict[str, Any]:
    """Отправить постоянное Rich-сообщение."""
    if (markdown is None) == (html is None):
        raise ValueError("Нужно передать ровно одно поле: markdown или html")

    rich_message: dict[str, Any] = {
        "skip_entity_detection": skip_entity_detection,
    }
    if markdown is not None:
        rich_message["markdown"] = markdown
    else:
        rich_message["html"] = html

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": rich_message,
        "disable_notification": disable_notification,
        "protect_content": protect_content,
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id

    return await _call("sendRichMessage", payload)


async def edit_rich_message(
    chat_id: int | str,
    message_id: int,
    *,
    markdown: str | None = None,
    html: str | None = None,
) -> dict[str, Any]:
    """Заменить ранее отправленный Rich-пост содержимым текущего черновика."""
    if (markdown is None) == (html is None):
        raise ValueError("Нужно передать ровно одно поле: markdown или html")

    rich_message = {"markdown": markdown} if markdown is not None else {"html": html}
    return await _call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": rich_message,
        },
    )


async def send_rich_message_draft(
    chat_id: int,
    draft_id: int,
    *,
    markdown: str | None = None,
    html: str | None = None,
) -> bool:
    """Показать временный потоковый черновик примерно на 30 секунд."""
    if not draft_id:
        raise ValueError("draft_id должен быть ненулевым")
    if (markdown is None) == (html is None):
        raise ValueError("Нужно передать ровно одно поле: markdown или html")

    rich_message = {"markdown": markdown} if markdown is not None else {"html": html}
    result = await _call(
        "sendRichMessageDraft",
        {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "rich_message": rich_message,
        },
    )
    return bool(result)
