"""
config.py — загрузка настроек из .env и общие константы.
"""

import os

from dotenv import load_dotenv


# Загружаем переменные из файла .env.
load_dotenv()


# Получаем токен из переменной BOT_TOKEN.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN. Проверьте файл .env."
    )


# Получаем строку вида: 783631437,681184803
allowed_ids_string = os.getenv("ALLOWED_USER_IDS", "")

# Превращаем строку в список чисел:
# [783631437, 681184803]
ALLOWED_USER_IDS = [
    int(user_id.strip())
    for user_id in allowed_ids_string.split(",")
    if user_id.strip()
]


# Путь к файлу SQLite.
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()


# Базовый адрес Telegram Bot API.
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"