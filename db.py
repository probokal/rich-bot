"""
db.py — работа с базой SQLite через aiosqlite (асинхронно).
Храним: черновики, шаблоны и историю отправленных постов.
"""
import aiosqlite
from config import DB_PATH


async def init_db():
    """Создаём таблицы при первом запуске."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Черновики: один на пользователя (перезаписывается)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts (
                user_id   INTEGER PRIMARY KEY,
                markdown  TEXT,
                html      TEXT,
                updated_at INTEGER
            )
            """
        )
        # Шаблоны: именованные заготовки (несколько штук)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS templates (
                name     TEXT PRIMARY KEY,
                markdown TEXT,
                html     TEXT
            )
            """
        )
        # История отправленных постов (нужно для редактирования)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     TEXT,
                message_id  INTEGER,
                name        TEXT,
                created_at  INTEGER
            )
            """
        )
        await db.commit()


async def save_draft(user_id: int, markdown: str, html: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO drafts (user_id, markdown, html, updated_at) "
            "VALUES (?, ?, ?, strftime('%s','now'))",
            (user_id, markdown, html),
        )
        await db.commit()


async def load_draft(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT markdown, html FROM drafts WHERE user_id = ?", (user_id,)
        )
        return await cur.fetchone()


async def save_template(name: str, markdown: str, html: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO templates (name, markdown, html) VALUES (?, ?, ?)",
            (name, markdown, html),
        )
        await db.commit()


async def list_templates():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM templates ORDER BY name")
        return [r[0] for r in await cur.fetchall()]


async def load_template(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT markdown, html FROM templates WHERE name = ?", (name,)
        )
        return await cur.fetchone()


async def log_sent(chat_id, message_id, name=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sent_posts (chat_id, message_id, name, created_at) "
            "VALUES (?, ?, ?, strftime('%s','now'))",
            (str(chat_id), message_id, name),
        )
        await db.commit()
