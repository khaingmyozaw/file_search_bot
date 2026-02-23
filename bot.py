import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ChannelPostHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DB_PATH = Path(os.getenv("DB_PATH", "search_index.db"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "5"))
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))

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
            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_channels (
                channel_id INTEGER PRIMARY KEY,
                channel_title TEXT NOT NULL,
                channel_username TEXT,
                added_by INTEGER NOT NULL,
                added_at TEXT NOT NULL
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

    def ensure_owner(self, owner_user_id: int) -> None:
        if owner_user_id <= 0:
            return
        self.conn.execute(
            """
            INSERT OR IGNORE INTO bot_admins(user_id, added_at)
            VALUES(?, ?)
            """,
            (owner_user_id, datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def is_bot_admin(self, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    def list_bot_admins(self) -> List[int]:
        rows = self.conn.execute("SELECT user_id FROM bot_admins ORDER BY user_id").fetchall()
        return [row["user_id"] for row in rows]

    def add_bot_admin(self, user_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO bot_admins(user_id, added_at) VALUES(?, ?)",
            (user_id, datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def remove_bot_admin(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM bot_admins WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def add_allowed_channel(
        self,
        channel_id: int,
        channel_title: str,
        channel_username: Optional[str],
        added_by: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO allowed_channels(channel_id, channel_title, channel_username, added_by, added_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(channel_id)
            DO UPDATE SET
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username,
                added_by=excluded.added_by,
                added_at=excluded.added_at
            """,
            (
                channel_id,
                channel_title,
                channel_username,
                added_by,
                datetime.utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def remove_allowed_channel(self, channel_id: int) -> None:
        self.conn.execute("DELETE FROM allowed_channels WHERE channel_id = ?", (channel_id,))
        self.conn.commit()

    def list_allowed_channels(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT channel_id, channel_title, channel_username FROM allowed_channels ORDER BY channel_title"
        ).fetchall()

    def is_allowed_channel(self, channel_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM allowed_channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return row is not None

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
            JOIN allowed_channels c ON c.channel_id = m.channel_id
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
        lines.append("   (Private channel: open it manually in Telegram.)")
    return "\n".join(lines)


def actor_user_id(update: Update) -> Optional[int]:
    if update.effective_user:
        return update.effective_user.id
    return None


async def require_bot_admin(update: Update) -> Optional[int]:
    user_id = actor_user_id(update)
    if user_id is None or not db.is_bot_admin(user_id):
        if update.effective_message:
            await update.effective_message.reply_text(
                "Only bot managers can use this command."
            )
        return None
    return user_id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 <b>Hi! I can search channel posts.</b>\n\n"
        "Normal usage:\n"
        "1) Ask your bot manager to approve channels first.\n"
        "2) Send any keywords, e.g. <code>invoice March</code>\n"
        "3) I will show the top matching posts.\n\n"
        "Bot manager commands:\n"
        "/add_admin &lt;user_id&gt;\n"
        "/remove_admin &lt;user_id&gt;\n"
        "/list_admins\n"
        "/allow_channel &lt;channel_id&gt;\n"
        "/remove_channel &lt;channel_id&gt;\n"
        "/list_channels\n"
        "/sync_help\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager_id = await require_bot_admin(update)
    if manager_id is None:
        return
    if manager_id != OWNER_USER_ID:
        await update.effective_message.reply_text("Only OWNER_USER_ID can add bot managers.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /add_admin <user_id>")
        return
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("user_id must be a number.")
        return
    db.add_bot_admin(new_admin_id)
    await update.effective_message.reply_text(f"Added bot manager: {new_admin_id}")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager_id = await require_bot_admin(update)
    if manager_id is None:
        return
    if manager_id != OWNER_USER_ID:
        await update.effective_message.reply_text("Only OWNER_USER_ID can remove bot managers.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /remove_admin <user_id>")
        return
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("user_id must be a number.")
        return
    if admin_id == OWNER_USER_ID:
        await update.effective_message.reply_text("Owner cannot be removed.")
        return
    db.remove_bot_admin(admin_id)
    await update.effective_message.reply_text(f"Removed bot manager: {admin_id}")


async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager_id = await require_bot_admin(update)
    if manager_id is None:
        return
    admins = db.list_bot_admins()
    await update.effective_message.reply_text(
        "Bot managers:\n" + "\n".join(str(admin) for admin in admins)
    )


async def allow_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager_id = await require_bot_admin(update)
    if manager_id is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /allow_channel <channel_id>")
        return
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("channel_id must be a number.")
        return

    try:
        chat = await context.bot.get_chat(channel_id)
    except Exception:
        await update.effective_message.reply_text(
            "I can't access this channel. Add me as admin first, then retry."
        )
        return

    if chat.type != ChatType.CHANNEL:
        await update.effective_message.reply_text("This chat_id is not a channel.")
        return

    db.add_allowed_channel(chat.id, chat.title or "Unknown Channel", chat.username, manager_id)
    await update.effective_message.reply_text(
        f"Channel allowed: {chat.title or chat.id} ({chat.id})"
    )


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager_id = await require_bot_admin(update)
    if manager_id is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /remove_channel <channel_id>")
        return
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("channel_id must be a number.")
        return
    db.remove_allowed_channel(channel_id)
    await update.effective_message.reply_text(f"Channel removed: {channel_id}")


async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager_id = await require_bot_admin(update)
    if manager_id is None:
        return
    channels = db.list_allowed_channels()
    if not channels:
        await update.effective_message.reply_text("No channels are allowed yet.")
        return
    lines = ["Allowed channels:"]
    for row in channels:
        username = f"@{row['channel_username']}" if row["channel_username"] else "(private)"
        lines.append(f"- {row['channel_title']} | {row['channel_id']} | {username}")
    await update.effective_message.reply_text("\n".join(lines))


async def sync_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Old data sync (manual but safe):\n"
        "1) Open channel history.\n"
        "2) Forward old posts to this bot in private chat.\n"
        "3) I will index forwarded posts only if that source channel is allowed.\n\n"
        "This protects the bot from random channels being indexed without approval."
    )
    await update.effective_message.reply_text(text)


async def index_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return

    chat = post.chat
    if not db.is_allowed_channel(chat.id):
        logger.info("Ignored post from unapproved channel %s", chat.id)
        return

    text = post.text or post.caption
    if not text:
        return

    db.upsert_message(
        channel_id=chat.id,
        channel_title=chat.title or "Unknown Channel",
        channel_username=chat.username,
        message_id=post.message_id,
        posted_at=post.date.isoformat(),
        text=text,
    )
    logger.info("Indexed message %s from approved channel %s", post.message_id, chat.id)


async def index_forwarded_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.forward_origin:
        return

    user_id = actor_user_id(update)
    if user_id is None or not db.is_bot_admin(user_id):
        return

    # Forwarded channel data is available on forward_from_chat for channel forwards.
    from_chat = msg.forward_from_chat
    if from_chat is None or from_chat.type != ChatType.CHANNEL:
        return

    if not db.is_allowed_channel(from_chat.id):
        await msg.reply_text(
            "This source channel is not approved. Ask a bot manager to /allow_channel first."
        )
        return

    text = msg.text or msg.caption
    if not text:
        return

    message_id = msg.forward_from_message_id or msg.message_id
    db.upsert_message(
        channel_id=from_chat.id,
        channel_title=from_chat.title or "Unknown Channel",
        channel_username=from_chat.username,
        message_id=message_id,
        posted_at=msg.date.isoformat(),
        text=text,
    )
    await msg.reply_text("Synced 1 old post into search index ✅")


async def search_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    if message.chat.type != ChatType.PRIVATE:
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


async def verify_owner_is_admin(app: Application) -> None:
    if OWNER_USER_ID <= 0:
        raise RuntimeError("Set OWNER_USER_ID to a valid Telegram user id.")
    db.ensure_owner(OWNER_USER_ID)


async def post_init(app: Application) -> None:
    await verify_owner_is_admin(app)


async def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN environment variable before starting the bot.")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("list_admins", list_admins))
    app.add_handler(CommandHandler("allow_channel", allow_channel))
    app.add_handler(CommandHandler("remove_channel", remove_channel))
    app.add_handler(CommandHandler("list_channels", list_channels))
    app.add_handler(CommandHandler("sync_help", sync_help))
    app.add_handler(ChannelPostHandler(index_channel_post))
    app.add_handler(MessageHandler(filters.FORWARDED, index_forwarded_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_messages))

    logger.info("Bot started.")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
