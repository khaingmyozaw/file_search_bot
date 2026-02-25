from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Config
from indexer import sync_old_messages
from models import Channel
from search import channels_with_stats, find_channel_by_identifier, upsert_channel


def build_admin_router(config: Config, session_factory: async_sessionmaker[AsyncSession]) -> Router:
    router = Router(name='admin')

    def is_admin(user_id: int | None) -> bool:
        return user_id is not None and user_id in config.admin_ids

    async def reject_non_admin(message: Message) -> bool:
        if is_admin(message.from_user.id if message.from_user else None):
            return False
        await message.answer('Admin only command.')
        return True

    @router.message(Command('admin'))
    async def admin_help(message: Message) -> None:
        if await reject_non_admin(message):
            return

        text = (
            '/add_channel <@username or -100id>\n'
            '/remove_channel <identifier>\n'
            '/channels\n'
            '/sync_channel <identifier>\n'
            '/stats'
        )
        await message.answer(text)

    @router.message(Command('add_channel'))
    async def add_channel(message: Message) -> None:
        if await reject_non_admin(message):
            return

        args = (message.text or '').split(maxsplit=1)
        if len(args) < 2:
            await message.answer('Usage: /add_channel <@username or -100id>')
            return

        identifier = args[1].strip()

        try:
            chat = await message.bot.get_chat(identifier)
        except TelegramBadRequest as exc:
            await message.answer(f'Cannot access channel: {exc.message}')
            return

        if chat.type not in {'channel', 'supergroup'}:
            await message.answer('Only channel/supergroup chats are supported.')
            return

        async with session_factory() as session:
            channel = await upsert_channel(
                session=session,
                tg_channel_id=chat.id,
                title=chat.title or str(chat.id),
                username=chat.username,
            )

        await message.answer(f'Channel saved: {channel.title} ({channel.tg_channel_id})')

    @router.message(Command('remove_channel'))
    async def remove_channel(message: Message) -> None:
        if await reject_non_admin(message):
            return

        args = (message.text or '').split(maxsplit=1)
        if len(args) < 2:
            await message.answer('Usage: /remove_channel <identifier>')
            return

        async with session_factory() as session:
            channel = await find_channel_by_identifier(session, args[1])
            if channel is None:
                await message.answer('Channel not found.')
                return

            await session.execute(delete(Channel).where(Channel.id == channel.id))
            await session.commit()

        await message.answer(f'Removed: {channel.title}')

    @router.message(Command('channels'))
    async def list_channels(message: Message) -> None:
        if await reject_non_admin(message):
            return

        async with session_factory() as session:
            rows = await session.scalars(select(Channel).order_by(Channel.title.asc()))
            channels = rows.all()

        if not channels:
            await message.answer('No tracked channels.')
            return

        lines = []
        for ch in channels:
            ident = f'@{ch.username}' if ch.username else str(ch.tg_channel_id)
            lines.append(f'- {ch.title} ({ident})')
        await message.answer('\n'.join(lines))

    @router.message(Command('sync_channel'))
    async def sync_channel(message: Message) -> None:
        if await reject_non_admin(message):
            return

        args = (message.text or '').split(maxsplit=1)
        if len(args) < 2:
            await message.answer('Usage: /sync_channel <identifier>')
            return

        identifier = args[1].strip()
        progress_message = await message.answer('Sync started...')

        async def on_progress(saved: int, total: int) -> None:
            if total == 0 or total % 200 != 0:
                return
            await progress_message.edit_text(f'Sync in progress: {saved}/{total}')

        try:
            saved, total = await sync_old_messages(config, session_factory, identifier, on_progress)
        except Exception as exc:  # noqa: BLE001
            await progress_message.edit_text(f'Sync failed: {exc}')
            return

        await progress_message.edit_text(f'Sync done. Indexed {saved} messages from {total} fetched.')

    @router.message(Command('stats'))
    async def stats(message: Message) -> None:
        if await reject_non_admin(message):
            return

        async with session_factory() as session:
            rows = await channels_with_stats(session)

        if not rows:
            await message.answer('No stats yet. Add and sync channels first.')
            return

        lines = [f'- {channel.title}: {count}' for channel, count in rows]
        await message.answer('\n'.join(lines))

    return router
