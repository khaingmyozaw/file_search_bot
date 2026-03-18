import secrets
import time
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Config
from indexer import sync_old_messages
from models import Channel
from search import channels_with_stats, find_channel_by_identifier, upsert_channel


@dataclass(slots=True)
class RemoveChannelContext:
    admin_user_id: int
    channel_id: int
    created_at: float


REMOVE_CHANNEL_CONTEXT_TTL_SECONDS = 300
REMOVE_CHANNEL_CONTEXTS: dict[str, RemoveChannelContext] = {}


def prune_remove_channel_contexts() -> None:
    now = time.time()
    stale_keys = [
        key
        for key, ctx in REMOVE_CHANNEL_CONTEXTS.items()
        if now - ctx.created_at > REMOVE_CHANNEL_CONTEXT_TTL_SECONDS
    ]
    for key in stale_keys:
        REMOVE_CHANNEL_CONTEXTS.pop(key, None)


def build_remove_options_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='Keep history only', callback_data=f'rmch:keep:{token}'),
                InlineKeyboardButton(text='Delete all history', callback_data=f'rmch:purge:{token}'),
            ]
        ]
    )


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
            '/purge_channel <identifier>\n'
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

        user = message.from_user
        if user is None:
            await message.answer('Cannot validate user for this command.')
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

        is_channel_accessible = True
        try:
            await message.bot.get_chat(channel.tg_channel_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            is_channel_accessible = False

        if not is_channel_accessible:
            prune_remove_channel_contexts()
            token = secrets.token_urlsafe(6)
            REMOVE_CHANNEL_CONTEXTS[token] = RemoveChannelContext(
                admin_user_id=user.id,
                channel_id=channel.id,
                created_at=time.time(),
            )
            await message.answer(
                (
                    f'Channel appears deleted or inaccessible: {channel.title}\n\n'
                    'Do you want to delete all indexed history too?'
                ),
                reply_markup=build_remove_options_keyboard(token),
            )
            return

        async with session_factory() as session:
            await session.execute(update(Channel).where(Channel.id == channel.id).values(is_tracked=False))
            await session.commit()

        await message.answer(f'Removed from tracking (history kept): {channel.title}')

    @router.callback_query(F.data.startswith('rmch:'))
    async def handle_remove_channel_decision(callback: CallbackQuery) -> None:
        user = callback.from_user
        payload = (callback.data or '').split(':', maxsplit=2)
        if len(payload) != 3:
            await callback.answer('Invalid action.', show_alert=True)
            return

        _, action, token = payload
        prune_remove_channel_contexts()
        context = REMOVE_CHANNEL_CONTEXTS.pop(token, None)
        if context is None:
            await callback.answer('This action expired. Run /remove_channel again.', show_alert=True)
            return

        if user.id != context.admin_user_id:
            await callback.answer('This action belongs to another admin.', show_alert=True)
            return

        async with session_factory() as session:
            channel = await session.scalar(select(Channel).where(Channel.id == context.channel_id))
            if channel is None:
                await callback.answer('Channel no longer exists.', show_alert=True)
                return

            if action == 'keep':
                await session.execute(update(Channel).where(Channel.id == channel.id).values(is_tracked=False))
                await session.commit()
                text = f'Removed from tracking (history kept): {channel.title}'
            elif action == 'purge':
                await session.execute(delete(Channel).where(Channel.id == channel.id))
                await session.commit()
                text = f'Removed and purged history: {channel.title}'
            else:
                await callback.answer('Unknown action.', show_alert=True)
                return

        if callback.message is not None:
            await callback.message.edit_text(text)
        await callback.answer('Done.')

    @router.message(Command('channels'))
    async def list_channels(message: Message) -> None:
        async with session_factory() as session:
            rows = await session.scalars(select(Channel).order_by(Channel.title.asc()))
            channels = rows.all()

        if not channels:
            await message.answer('No tracked channels.')
            return

        lines = []
        for ch in channels:
            ident = f'@{ch.username}' if ch.username else str(ch.tg_channel_id)
            status = 'tracked' if ch.is_tracked else 'history-only'
            lines.append(f'- {ch.title} ({ident}) [{status}]')
        await message.answer('\n'.join(lines))

    @router.message(Command('purge_channel'))
    async def purge_channel(message: Message) -> None:
        if await reject_non_admin(message):
            return

        args = (message.text or '').split(maxsplit=1)
        if len(args) < 2:
            await message.answer('Usage: /purge_channel <identifier>')
            return

        async with session_factory() as session:
            channel = await find_channel_by_identifier(session, args[1])
            if channel is None:
                await message.answer('Channel not found.')
                return

            await session.execute(delete(Channel).where(Channel.id == channel.id))
            await session.commit()

        await message.answer(f'Purged channel and all history: {channel.title}')

    @router.message(Command('sync_channel'))
    async def sync_channel(message: Message) -> None:
        if await reject_non_admin(message):
            return

        args = (message.text or '').split(maxsplit=1)
        if len(args) < 2:
            await message.answer('Usage: /sync_channel <identifier>')
            return

        identifier = args[1].strip()
        progress_message = await message.answer('Sync started (newest -> oldest)...')

        async def on_progress(saved: int, total: int) -> None:
            if total == 0 or total % 200 != 0:
                return
            await progress_message.edit_text(f'Sync in progress: {saved}/{total}')

        try:
            saved, total = await sync_old_messages(config, session_factory, identifier, on_progress)
        except Exception as exc:  # noqa: BLE001
            await progress_message.edit_text(f'Sync failed: {exc}')
            return

        await progress_message.edit_text(
            f'Sync done (newest -> oldest). Indexed {saved} messages from {total} fetched.'
        )

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
