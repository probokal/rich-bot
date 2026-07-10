"""
uploader.py - превращает присланный в бота файл в прямую HTTPS-ссылку.

Для Rich Message медиа задаются отдельными блоками с HTTP/HTTPS URL.
Когда пользователь присылает фото/видео/аудио/гиф прямо в чат:

1) бот получает file_id через aiogram;
2) запрашивает у Telegram file_path через getFile;
3) скачивает файл;
4) заливает его на бесплатный файлохостинг 0x0.st;
5) возвращает прямую HTTPS-ссылку.

Если 0x0.st не работает - замените UPLOAD_URL на https://catbox.moe/user/api.php
и скорректируйте формат запроса.
"""
from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path
from typing import Any

import httpx
from aiogram import Bot
from aiogram.types import Message

from config import BOT_TOKEN

FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}/"
UPLOAD_URL = "https://0x0.st"


def _extract_media(message: Message) -> tuple[str, str | None, str | None]:
    """Вернуть (file_id, имя файла, mime-тип) для присланного медиа."""
    if message.photo:
        largest = max(message.photo, key=lambda p: p.file_size or 0)
        return largest.file_id, f"photo-{largest.file_unique_id}.jpg", "image/jpeg"
    if message.video:
        return message.video.file_id, message.video.file_name, message.video.mime_type
    if message.audio:
        return message.audio.file_id, message.audio.file_name, message.audio.mime_type
    if message.animation:
        return message.animation.file_id, message.animation.file_name, "image/gif"
    if message.voice:
        return message.voice.file_id, f"voice-{message.voice.file_unique_id}.ogg", message.voice.mime_type or "audio/ogg"
    if message.video_note:
        return message.video_note.file_id, f"video-note-{message.video_note.file_unique_id}.mp4", "video/mp4"
    if message.document:
        return message.document.file_id, message.document.file_name, message.document.mime_type
    raise ValueError("В сообщении нет медиафайла")


def _guess_suffix(name: str | None, mime: str | None) -> str:
    if name and "." in name:
        return Path(name).suffix
    if mime:
        guess = mimetypes.guess_extension(mime)
        if guess:
            return guess
    return ".bin"


async def upload_user_media(bot: Bot, message: Message) -> tuple[str, str, str]:
    """Скачать присланный файл, залить на хостинг, вернуть (url, filename, mime)."""
    file_id, original_name, original_mime = _extract_media(message)

    tg_file = await bot.get_file(file_id)
    download_url = FILE_API + tg_file.file_path

    suffix = _guess_suffix(original_name, original_mime)

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(download_url)
        r.raise_for_status()
        data = r.content

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            with tmp_path.open("rb") as f:
                files: Any = {"file": (original_name or tmp_path.name, f, original_mime or "application/octet-stream")}
                up = await client.post(UPLOAD_URL, files=files)
            up.raise_for_status()
            url = up.text.strip()
    finally:
        tmp_path.unlink(missing_ok=True)

    if not url.startswith("http"):
        raise RuntimeError(f"Хостинг не вернул ссылку: {up.text[:200]}")

    return url, original_name or "", original_mime or ""