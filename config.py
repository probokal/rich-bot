"""config.py - безопасная загрузка настроек из .env / Railway Variables."""
import hashlib
import os

from dotenv import load_dotenv


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Проверьте .env или Railway Variables.")


def _read_allowed_users() -> list[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not raw:
        return []
    try:
        return [int(value.strip()) for value in raw.split(",") if value.strip()]
    except ValueError as error:
        raise RuntimeError("ALLOWED_USER_IDS должен содержать числа через запятую") from error


ALLOWED_USER_IDS = _read_allowed_users()
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Локально оставьте пустым: бот использует getUpdates (polling).
# На Railway укажите публичный домен, например:
# WEBHOOK_BASE_URL=https://richbot-production.up.railway.app
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
WEBHOOK_PATH = "/telegram-webhook"

# Telegram присылает этот секрет в заголовке каждого webhook-запроса.
# Если переменная не задана, создается стабильное безопасное значение из токена.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip() or hashlib.sha256(
    f"rich-post:{BOT_TOKEN}".encode("utf-8")
).hexdigest()

# Railway сам задает PORT. Локальный запасной порт - 8080.
PORT = int(os.getenv("PORT", "8080"))
