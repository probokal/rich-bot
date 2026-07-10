"""
uploader.py - загрузка присланного медиа на публичный файловый хостинг.

Rich Message принимает медиа только по HTTP/HTTPS URL. Поэтому бот скачивает
присланный файл из Telegram и загружает копию на внешний хостинг:

1. Catbox - основной вариант, ссылки постоянные, до 200 МБ;
2. 0x0.st - резервный вариант, если Catbox временно недоступен.

Оба сервиса сторонние. Не отправляйте через них приватные файлы. Для важного
проекта лучше подключить собственное S3/R2-хранилище.
"""
import asyncio
import mimetypes
import os
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.types import Message

from config import BOT_TOKEN


TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}/"
CATBOX_API = "https://catbox.moe/user/api.php"
ZEROX_API = "https://0x0.st"
CATBOX_USERHASH = os.getenv("CATBOX_USERHASH", "").strip()

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
RETRY_DELAYS = (0, 2, 5)
HEADERS = {
    "User-Agent": "RichPostTelegramBot/1.0 (+https://core.telegram.org/bots/api)",
}


class MediaUploadError(RuntimeError):
    """Понятная ошибка скачивания или загрузки медиа."""


def _extract_media(message: Message) -> tuple[str, str, str]:
    """Вернуть file_id, безопасное имя файла и MIME-тип."""
    if message.photo:
        largest = max(message.photo, key=lambda item: item.file_size or 0)
        return largest.file_id, f"photo-{largest.file_unique_id}.jpg", "image/jpeg"
    if message.video:
        return (
            message.video.file_id,
            message.video.file_name or f"video-{message.video.file_unique_id}.mp4",
            message.video.mime_type or "video/mp4",
        )
    if message.audio:
        return (
            message.audio.file_id,
            message.audio.file_name or f"audio-{message.audio.file_unique_id}.mp3",
            message.audio.mime_type or "audio/mpeg",
        )
    if message.animation:
        return (
            message.animation.file_id,
            message.animation.file_name or f"animation-{message.animation.file_unique_id}.gif",
            message.animation.mime_type or "image/gif",
        )
    if message.voice:
        return (
            message.voice.file_id,
            f"voice-{message.voice.file_unique_id}.ogg",
            message.voice.mime_type or "audio/ogg",
        )
    if message.video_note:
        return (
            message.video_note.file_id,
            f"video-note-{message.video_note.file_unique_id}.mp4",
            "video/mp4",
        )
    if message.document:
        mime = message.document.mime_type or "application/octet-stream"
        extension = mimetypes.guess_extension(mime) or ".bin"
        name = message.document.file_name or f"document-{message.document.file_unique_id}{extension}"
        return message.document.file_id, name, mime
    raise MediaUploadError("В сообщении нет поддерживаемого медиафайла.")


def _safe_name(file_name: str) -> str:
    """Убрать путь и символы, которые могут мешать multipart-загрузке."""
    name = Path(file_name).name.replace("\x00", "").strip()
    return name or "telegram-file.bin"


def _check_url(value: str, provider: str) -> str:
    """Проверить, что хостинг действительно вернул прямую ссылку."""
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        raise MediaUploadError(f"{provider} не вернул ссылку: {url[:160]}")
    return url


async def _download_from_telegram(bot: Bot, file_id: str) -> bytes:
    """Скачать файл с серверов Telegram в память."""
    tg_file = await bot.get_file(file_id)
    if not tg_file.file_path:
        raise MediaUploadError("Telegram не вернул путь к файлу.")

    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=HEADERS) as client:
        response = await client.get(TELEGRAM_FILE_API + tg_file.file_path)
        response.raise_for_status()
        content = response.content

    if not content:
        raise MediaUploadError("Telegram вернул пустой файл.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise MediaUploadError("Файл больше 200 МБ и не помещается в Catbox.")
    return content


async def _upload_catbox(content: bytes, name: str, mime: str) -> str:
    """Загрузить файл на Catbox по multipart API."""
    data = {
        "reqtype": "fileupload",
        "userhash": CATBOX_USERHASH,
    }
    files = {
        "fileToUpload": (name, content, mime),
    }
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, headers=HEADERS) as client:
        response = await client.post(CATBOX_API, data=data, files=files)
        response.raise_for_status()
        return _check_url(response.text, "Catbox")


async def _upload_0x0(content: bytes, name: str, mime: str) -> str:
    """Резервная загрузка на 0x0.st."""
    files = {
        "file": (name, content, mime),
    }
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, headers=HEADERS) as client:
        response = await client.post(ZEROX_API, files=files)
        response.raise_for_status()
        return _check_url(response.text, "0x0.st")


async def _with_retries(
    provider_name: str,
    upload_function,
    content: bytes,
    name: str,
    mime: str,
) -> tuple[str | None, str | None]:
    """Повторить временно неудачную загрузку до трех раз."""
    last_error = "неизвестная ошибка"
    for delay in RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            url = await upload_function(content, name, mime)
            return url, None
        except (httpx.HTTPError, MediaUploadError) as error:
            last_error = str(error)
    return None, f"{provider_name}: {last_error}"


async def upload_user_media(bot: Bot, message: Message) -> tuple[str, str, str]:
    """
    Скачать присланный файл и получить публичную ссылку.

    Сначала пробуется Catbox, затем резервный 0x0.st. Возвращает:
    (url, исходное имя, MIME-тип).
    """
    file_id, original_name, original_mime = _extract_media(message)
    name = _safe_name(original_name)

    try:
        content = await _download_from_telegram(bot, file_id)
    except httpx.HTTPError as error:
        raise MediaUploadError(f"Не удалось скачать файл из Telegram: {error}") from error

    errors: list[str] = []
    providers = (
        ("Catbox", _upload_catbox),
        ("0x0.st", _upload_0x0),
    )

    for provider_name, upload_function in providers:
        url, error = await _with_retries(
            provider_name,
            upload_function,
            content,
            name,
            original_mime,
        )
        if url:
            return url, name, original_mime
        if error:
            errors.append(error)

    details = "; ".join(errors)
    raise MediaUploadError(
        "внешние хостинги временно недоступны. Попробуйте позже или пришлите "
        f"прямую HTTPS-ссылку. Технические детали: {details}"
    )
