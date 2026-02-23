import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DB_PATH = Path(os.getenv("DB_PATH", "search_index.db"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "5"))

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger("file-search-bot")


@dataclass
class SearchResult:
    channel_id: int
    channel_title: str
    channel_username: Optional[str]
    message_id: int
    posted_at: str
    text: str


class SearchDB:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                channel_id INTEGER NOT NULL,
                channel_title TEXT NOT NULL,
                channel_username TEXT,
                message_id INTEGER NOT NULL,
                posted_at TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY(channel_id, message_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(
                text,
                channel_title,
                content='messages',
                content_rowid='rowid'
            )
            """
        )
        self.conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, text, channel_title)
                VALUES (new.rowid, new.text, new.channel_title);
            END
            """
        )
        self.conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text, channel_title)
                VALUES ('delete', old.rowid, old.text, old.channel_title);
                INSERT INTO messages_fts(rowid, text, channel_title)
                VALUES (new.rowid, new.text, new.channel_title);
            END
            """
        )
        self.conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text, channel_title)
                VALUES ('delete', old.rowid, old.text, old.channel_title);
            END
            """
        )
        self.conn.commit()

    def upsert_message(
        self,
        channel_id: int,
        channel_title: str,
        channel_username: Optional[str],
        message_id: int,
        posted_at: str,
        text: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO messages(channel_id, channel_title, channel_username, message_id, posted_at, text)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, message_id)
            DO UPDATE SET
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username,
                posted_at=excluded.posted_at,
                text=excluded.text
            """,
            (channel_id, channel_title, channel_username, message_id, posted_at, text),
        )
        self.conn.commit()

    def search(self, query: str, limit: int = 5) -> List[SearchResult]:
        rows = self.conn.execute(
            """
            SELECT m.channel_id, m.channel_title, m.channel_username, m.message_id, m.posted_at, m.text
            FROM messages_fts f
            JOIN messages m ON m.rowid = f.rowid
            WHERE messages_fts MATCH ?
            ORDER BY bm25(messages_fts)
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [SearchResult(**dict(row)) for row in rows]


db = SearchDB(DB_PATH)


def build_message_link(result: SearchResult) -> Optional[str]:
    if not result.channel_username:
        return None
    return f"https://t.me/{result.channel_username}/{result.message_id}"


def format_result(result: SearchResult, index: int) -> str:
    snippet = result.text.strip().replace("\n", " ")
    if len(snippet) > 160:
        snippet = snippet[:157] + "..."
    date_text = datetime.fromisoformat(result.posted_at).strftime("%Y-%m-%d %H:%M")
    link = build_message_link(result)

    lines = [f"{index}. <b>{result.channel_title}</b> ({date_text})", f"   {snippet}"]
    if link:
        lines.append(f"   <a href=\"{link}\">Open message</a>")
    else:
        lines.append("   (This channel is private, open it from Telegram manually.)")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 <b>Hi! I can search posts from your channels.</b>\n\n"
        "How to use me:\n"
        "1) Add me as <b>admin</b> to each channel you want to index.\n"
        "2) Keep posting normally — I save new posts automatically.\n"
        "3) Send me any keyword, for example: <code>invoice March</code>\n\n"
        "Commands:\n"
        "/start - show this guide\n"
        "/help - quick help\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def index_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return

    text = post.text or post.caption
    if not text:
        return

    chat = post.chat
    posted_at = post.date.isoformat()
    db.upsert_message(
        channel_id=chat.id,
        channel_title=chat.title or "Unknown Channel",
        channel_username=chat.username,
        message_id=post.message_id,
        posted_at=posted_at,
        text=text,
    )
    logger.info("Indexed message %s from %s", post.message_id, chat.title)


async def search_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    query = message.text.strip()
    if len(query) < 2:
        await message.reply_text("Please type at least 2 characters to search.")
        return

    try:
        results = db.search(query, limit=MAX_RESULTS)
    except sqlite3.OperationalError:
        await message.reply_text(
            "I couldn't understand that search. Try simple keywords like: budget report"
        )
        return

    if not results:
        await message.reply_text("No results found. Try another keyword.")
        return

    formatted = [format_result(result, i + 1) for i, result in enumerate(results)]
    reply = "🔎 <b>Search results</b>\n\n" + "\n\n".join(formatted)
    await message.reply_text(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN environment variable before starting the bot.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, index_channel_post))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, search_messages)
    )

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
