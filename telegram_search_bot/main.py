import asyncio
import logging

from aiogram import F, Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from admin import build_admin_router
from config import Config, load_config
from db import create_engine_and_session_factory, init_db
from indexer import index_channel_post
from search import search_messages

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger('telegram-search-bot')


async def forward_results(message: Message, keyword: str, config: Config, session_factory) -> None:
    async with session_factory() as session:
        rows = await search_messages(session, keyword=keyword, limit=config.search_limit)

    if not rows:
        await message.answer('No matches found.')
        return

    sent: set[tuple[int, int]] = set()
    for db_message, channel in rows:
        key = (channel.tg_channel_id, db_message.tg_message_id)
        if key in sent:
            continue
        sent.add(key)

        try:
            await message.bot.forward_message(
                chat_id=message.chat.id,
                from_chat_id=channel.tg_channel_id,
                message_id=db_message.tg_message_id,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(f'Cannot forward message from {channel.title}')


async def create_dispatcher(config: Config) -> Dispatcher:
    engine, session_factory = create_engine_and_session_factory(config.db_url)
    await init_db(engine)

    dp = Dispatcher()

    admin_router = build_admin_router(config, session_factory)
    dp.include_router(admin_router)

    user_router = Router(name='user')

    @user_router.message(Command('start'))
    async def start(message: Message) -> None:
        await message.answer('Send /search <keyword> or just type a keyword.')

    @user_router.message(Command('search'))
    async def search_cmd(message: Message) -> None:
        query = (message.text or '').split(maxsplit=1)
        if len(query) < 2 or not query[1].strip():
            await message.answer('Usage: /search <keyword>')
            return
        await forward_results(message, query[1], config, session_factory)

    @user_router.message(F.text)
    async def plain_text_search(message: Message) -> None:
        text = (message.text or '').strip()
        if not text or text.startswith('/'):
            return
        await forward_results(message, text, config, session_factory)

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
