import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

from aiogram import F, Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from admin import build_admin_router
from config import Config, load_config
from db import create_engine_and_session_factory, init_db
from indexer import index_channel_post
from search import search_messages

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger('telegram-search-bot')


@dataclass(slots=True)
class SearchContext:
    user_id: int
    keyword: str
    media_type: str | None
    created_at: float


SEARCH_CONTEXT_TTL_SECONDS = 300
SEARCH_CONTEXTS: dict[str, SearchContext] = {}
MEDIA_TYPES = {'photo', 'video', 'document', 'audio'}


def prune_search_contexts() -> None:
    now = time.time()
    stale_keys = [key for key, ctx in SEARCH_CONTEXTS.items() if now - ctx.created_at > SEARCH_CONTEXT_TTL_SECONDS]
    for key in stale_keys:
        SEARCH_CONTEXTS.pop(key, None)


def parse_media_keyword(raw: str, forced_media_type: str | None = None) -> tuple[str, str | None]:
    text = raw.strip()
    media_type = forced_media_type

    if text.lower().startswith('media:'):
        head, _, tail = text.partition(' ')
        candidate = head.split(':', 1)[1].strip().lower()
        if candidate in MEDIA_TYPES:
            media_type = candidate
            text = tail.strip()

    return text, media_type


def build_pagination_keyboard(token: str, offset: int, has_next: bool, page_size: int) -> InlineKeyboardMarkup | None:
    buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        prev_offset = max(0, offset - page_size)
        buttons.append(InlineKeyboardButton(text='Prev', callback_data=f'pg:{token}:{prev_offset}'))
    if has_next:
        next_offset = offset + page_size
        buttons.append(InlineKeyboardButton(text='Next', callback_data=f'pg:{token}:{next_offset}'))

    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_status_text(keyword: str, media_type: str | None, offset: int, shown_count: int, page_size: int) -> str:
    page_number = (offset // page_size) + 1
    media_part = f' | media: {media_type}' if media_type else ''
    return f'Results for "{keyword}"{media_part} | page {page_number} | shown: {shown_count}'


async def forward_results(
    message: Message,
    keyword: str,
    config: Config,
    session_factory,
    offset: int = 0,
    media_type: str | None = None,
) -> tuple[int, bool]:
    page_size = max(1, config.search_limit)
    async with session_factory() as session:
        rows = await search_messages(
            session,
            keyword=keyword,
            limit=page_size + 1,
            offset=offset,
            media_type=media_type,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    if not rows:
        if offset == 0:
            await message.answer('No matches found.')
        return 0, False

    sent: set[tuple[int, int]] = set()
    shown_count = 0
    for db_message, channel in rows:
        key = (channel.tg_channel_id, db_message.tg_message_id)
        if key in sent:
            continue
        sent.add(key)
        shown_count += 1

        try:
            await message.bot.forward_message(
                chat_id=message.chat.id,
                from_chat_id=channel.tg_channel_id,
                message_id=db_message.tg_message_id,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(f'Cannot forward message from {channel.title}')

    return shown_count, has_next


async def run_search_flow(
    message: Message,
    raw_query: str,
    config: Config,
    session_factory,
    forced_media_type: str | None = None,
) -> None:
    user = message.from_user
    if user is None:
        return

    keyword, media_type = parse_media_keyword(raw_query, forced_media_type=forced_media_type)
    if not keyword:
        await message.answer('Usage: /search <keyword> or /search media:photo <keyword>')
        return

    shown_count, has_next = await forward_results(
        message,
        keyword=keyword,
        config=config,
        session_factory=session_factory,
        offset=0,
        media_type=media_type,
    )
    if shown_count == 0:
        return

    prune_search_contexts()
    token = secrets.token_urlsafe(6)
    SEARCH_CONTEXTS[token] = SearchContext(
        user_id=user.id,
        keyword=keyword,
        media_type=media_type,
        created_at=time.time(),
    )
    keyboard = build_pagination_keyboard(token, offset=0, has_next=has_next, page_size=max(1, config.search_limit))
    if keyboard:
        await message.answer(
            build_status_text(keyword, media_type, offset=0, shown_count=shown_count, page_size=max(1, config.search_limit)),
            reply_markup=keyboard,
        )


async def create_dispatcher(config: Config) -> Dispatcher:
    engine, session_factory = create_engine_and_session_factory(config.db_url)
    await init_db(engine)

    dp = Dispatcher()

    admin_router = build_admin_router(config, session_factory)
    dp.include_router(admin_router)

    user_router = Router(name='user')

    @user_router.message(Command('start'))
    async def start(message: Message) -> None:
        await message.answer(
            'Send /search <keyword> or just type a keyword.\n'
            'Use media filter: /search media:photo <keyword>\n'
            'Quick commands: /photos <keyword>, /videos <keyword>, /documents <keyword>, /audio <keyword>'
        )

    @user_router.message(Command('help'))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            'Available commands:\n'
            '/search <keyword>\n'
            '/search media:<photo|video|document|audio> <keyword>\n'
            '/photos <keyword>\n'
            '/videos <keyword>\n'
            '/documents <keyword>\n'
            '/audio <keyword>\n'
            '/channels\n\n'
            'Tip: You can also type plain text to search.'
        )

    @user_router.message(Command('search'))
    async def search_cmd(message: Message) -> None:
        query = (message.text or '').split(maxsplit=1)
        if len(query) < 2 or not query[1].strip():
            await message.answer('Usage: /search <keyword> or /search media:photo <keyword>')
            return

        await run_search_flow(message, query[1], config, session_factory)

    @user_router.message(Command('photos'))
    async def photos_cmd(message: Message) -> None:
        query = (message.text or '').split(maxsplit=1)
        if len(query) < 2 or not query[1].strip():
            await message.answer('Usage: /photos <keyword>')
            return
        await run_search_flow(message, query[1], config, session_factory, forced_media_type='photo')

    @user_router.message(Command('videos'))
    async def videos_cmd(message: Message) -> None:
        query = (message.text or '').split(maxsplit=1)
        if len(query) < 2 or not query[1].strip():
            await message.answer('Usage: /videos <keyword>')
            return
        await run_search_flow(message, query[1], config, session_factory, forced_media_type='video')

    @user_router.message(Command('documents'))
    async def documents_cmd(message: Message) -> None:
        query = (message.text or '').split(maxsplit=1)
        if len(query) < 2 or not query[1].strip():
            await message.answer('Usage: /documents <keyword>')
            return
        await run_search_flow(message, query[1], config, session_factory, forced_media_type='document')

    @user_router.message(Command('audio'))
    async def audio_cmd(message: Message) -> None:
        query = (message.text or '').split(maxsplit=1)
        if len(query) < 2 or not query[1].strip():
            await message.answer('Usage: /audio <keyword>')
            return
        await run_search_flow(message, query[1], config, session_factory, forced_media_type='audio')

    @user_router.callback_query(F.data.startswith('pg:'))
    async def paginate_results(callback: CallbackQuery) -> None:
        user = callback.from_user
        if callback.message is None:
            await callback.answer('Cannot open this page here.', show_alert=True)
            return

        payload = (callback.data or '').split(':', maxsplit=2)
        if len(payload) != 3:
            await callback.answer('Invalid pagination action.', show_alert=True)
            return

        _, token, offset_text = payload
        context = SEARCH_CONTEXTS.get(token)
        if context is None:
            await callback.answer('Search session expired. Run search again.', show_alert=True)
            return
        if context.user_id != user.id:
            await callback.answer('This result set belongs to another user.', show_alert=True)
            return

        try:
            offset = max(0, int(offset_text))
        except ValueError:
            await callback.answer('Invalid page offset.', show_alert=True)
            return

        shown_count, has_next = await forward_results(
            callback.message,
            keyword=context.keyword,
            config=config,
            session_factory=session_factory,
            offset=offset,
            media_type=context.media_type,
        )

        if shown_count == 0:
            await callback.answer('No more matches.', show_alert=True)
            return

        keyboard = build_pagination_keyboard(
            token,
            offset=offset,
            has_next=has_next,
            page_size=max(1, config.search_limit),
        )
        if callback.message:
            await callback.message.edit_text(
                build_status_text(
                    context.keyword,
                    context.media_type,
                    offset=offset,
                    shown_count=shown_count,
                    page_size=max(1, config.search_limit),
                ),
                reply_markup=keyboard,
            )
        await callback.answer('Page loaded.')

    @user_router.message(F.text)
    async def plain_text_search(message: Message) -> None:
        text = (message.text or '').strip()
        if not text or text.startswith('/'):
            return
        await run_search_flow(message, text, config, session_factory)

    @user_router.channel_post()
    async def on_channel_post(message: Message) -> None:
        async with session_factory() as session:
            await index_channel_post(session, message)

    dp.include_router(user_router)
    return dp


async def run() -> None:
    config = load_config()
    bot = Bot(token=config.bot_token)
    dp = await create_dispatcher(config)

    logger.info('Bot started')
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(run())
