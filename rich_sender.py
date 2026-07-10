"""
rich_sender.py — отправка Rich-сообщений через метод sendRichMessage.
sendRichMessage — это «сырой» метод Bot API, поэтому шлём HTTP-запрос
напрямую через httpx. Именно он умеет принимать поля html/markdown.
"""
import httpx
from config import BOT_TOKEN


async def send_rich_message(
    chat_id,
    *,
    markdown: str | None = None,
    html: str | None = None,
    disable_notification: bool = False,
    protect_content: bool = False,
    message_thread_id: int | None = None,
    reply_markup: dict | None = None,
):
    """
    Отправляет Rich-пост. Нужно передать ТОЛЬКО ОДНО из полей:
    markdown ИЛИ html (по спецификации InputRichMessage).
    """
    if (markdown is None) == (html is None):
        raise ValueError("Передайте ровно одно поле: markdown ИЛИ html")

    # Собираем тело запроса
    payload = {
        "chat_id": chat_id,
        "rich_message": {},
        "disable_notification": disable_notification,   # тихая отправка
        "protect_content": protect_content,             # защита от пересылки/сохранения
    }
    if markdown:
        payload["rich_message"]["markdown"] = markdown
    else:
        payload["rich_message"]["html"] = html
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendRichMessage"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()  # {"ok": true, "result": {...Message...}}


async def send_rich_message_draft(chat_id: int, draft_id: int, markdown: str | None = None, html: str | None = None):
    """Потоковый черновик (живое превью). Живёт ~30 секунд."""
    if (markdown is None) == (html is None):
        raise ValueError("Передайте ровно одно поле: markdown ИЛИ html")
    payload = {"chat_id": chat_id, "draft_id": draft_id, "rich_message": {}}
    if markdown:
        payload["rich_message"]["markdown"] = markdown
    else:
        payload["rich_message"]["html"] = html
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendRichMessageDraft"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()