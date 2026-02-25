import logging
import os
import sqlite3
import shlex
import html
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

try:
    from telethon import TelegramClient, utils
    from telethon.sessions import StringSession
except ImportError:  # pragma: no cover - runtime fallback when dependency not installed
    TelegramClient = None
    utils = None
    StringSession = None


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if value:
            try:
                parsed = shlex.split(value, posix=True)
                if parsed:
                    value = parsed[0]
            except ValueError:
                pass
        os.environ[key] = value


load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "search_index.db"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "5"))
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))
TELETHON_API_ID = os.getenv("TELETHON_API_ID", "")
TELETHON_API_HASH = os.getenv("TELETHON_API_HASH", "")
TELETHON_SESSION = os.getenv("TELETHON_SESSION", "file_search_sync")
TELETHON_STRING_SESSION = os.getenv("TELETHON_STRING_SESSION", "")

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
        "/sync_channel &lt;channel_username_or_id&gt; [limit] - import old channel posts\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


def is_owner(update: Update) -> bool:
    user = update.effective_user
    return OWNER_USER_ID > 0 and user is not None and user.id == OWNER_USER_ID


async def build_sync_client() -> TelegramClient:
    if TelegramClient is None or utils is None or StringSession is None:
        raise RuntimeError("Install dependencies first: pip install -r requirements.txt")
    if not TELETHON_API_ID or not TELETHON_API_HASH:
        raise RuntimeError("Set TELETHON_API_ID and TELETHON_API_HASH in .env.")

    try:
        api_id = int(TELETHON_API_ID)
    except ValueError as exc:
        raise RuntimeError("TELETHON_API_ID must be a number.") from exc

    session = StringSession(TELETHON_STRING_SESSION) if TELETHON_STRING_SESSION else TELETHON_SESSION
    client = TelegramClient(session, api_id, TELETHON_API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telethon user session is not authorized. Generate TELETHON_STRING_SESSION first."
        )

    me = await client.get_me()
    if getattr(me, "bot", False):
        await client.disconnect()
        using_string_session = bool(TELETHON_STRING_SESSION)
        session_hint = (
            "TELETHON_STRING_SESSION is set, but it was generated from a bot login. "
            "Regenerate it by signing in as your personal Telegram account."
            if using_string_session
            else (
                "You are currently using local session file "
                f"'{TELETHON_SESSION}.session', which was logged in as a bot. "
                "Delete that file and set TELETHON_STRING_SESSION from a user login."
            )
        )
        raise RuntimeError(
            "Telethon session is authenticated as a bot. "
            f"{session_hint} Run: python scripts/generate_telethon_string_session.py"
        )

    return client


async def sync_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    if OWNER_USER_ID <= 0:
        await message.reply_text("Set OWNER_USER_ID in .env to enable /sync_channel.")
        return
    if not is_owner(update):
        await message.reply_text("Only OWNER_USER_ID can run /sync_channel.")
        return
    if not context.args:
        await message.reply_text("Usage: /sync_channel <channel_username_or_id> [limit]")
        return

    channel_ref = context.args[0]
    if channel_ref.startswith("@"):
        channel_ref = channel_ref[1:]

    limit = 500
    if len(context.args) > 1:
        try:
            limit = max(1, min(int(context.args[1]), 5000))
        except ValueError:
            await message.reply_text("limit must be a number (1..5000).")
            return

    await message.reply_text(
        f"Starting history sync for <code>{html.escape(channel_ref)}</code> (limit={limit})...",
        parse_mode=ParseMode.HTML,
    )

    imported = 0
    skipped = 0
    client = None
    try:
        client = await build_sync_client()
        entity = await client.get_entity(channel_ref)
        channel_id = utils.get_peer_id(entity)
        channel_title = getattr(entity, "title", "Unknown Channel")
        channel_username = getattr(entity, "username", None)

        async for post in client.iter_messages(entity, limit=limit):
            text = (post.message or "").strip()
            if not text:
                skipped += 1
                continue
            db.upsert_message(
                channel_id=channel_id,
                channel_title=channel_title,
                channel_username=channel_username,
                message_id=post.id,
                posted_at=post.date.isoformat(),
                text=text,
            )
            imported += 1
    except Exception as exc:
        logger.exception("History sync failed for %s", channel_ref)
        await message.reply_text(f"Sync failed: {exc}")
        return
    finally:
        if client is not None:
            await client.disconnect()

    await message.reply_text(
        "Sync complete.\n"
        f"Channel: {channel_ref}\n"
        f"Imported: {imported}\n"
        f"Skipped (no text/caption): {skipped}"
    )


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


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update: %s", context.error)
    effective_message = getattr(update, "effective_message", None)
    if effective_message:
        try:
            await effective_message.reply_text("Unexpected error. Please try again.")
        except Exception:
            pass


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN environment variable before starting the bot.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("sync_channel", sync_channel))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, index_channel_post))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, search_messages)
    )
    app.add_error_handler(on_error)

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
