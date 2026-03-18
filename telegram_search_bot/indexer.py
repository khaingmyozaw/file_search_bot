from collections.abc import Awaitable, Callable

from aiogram.types import Message as AiogramMessage
from telethon import TelegramClient, utils as telethon_utils
from telethon.tl.types import DocumentAttributeFilename
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Config
from models import Channel
from search import upsert_channel, upsert_message

ProgressCallback = Callable[[int, int], Awaitable[None]]


def extract_file_name_aiogram(message: AiogramMessage) -> str | None:
    if message.document and message.document.file_name:
        return message.document.file_name
    if message.audio and message.audio.file_name:
        return message.audio.file_name
    if message.video and message.video.file_name:
        return message.video.file_name
    return None


def detect_media_type_aiogram(message: AiogramMessage) -> str | None:
    if message.document:
        return 'document'
    if message.photo:
        return 'photo'
    if message.video:
        return 'video'
    if message.audio:
        return 'audio'
    if message.voice:
        return 'voice'
    if message.animation:
        return 'animation'
    if message.sticker:
        return 'sticker'
    return None


def extract_file_name_telethon(message) -> str | None:
    media = getattr(message, 'media', None)
    document = getattr(media, 'document', None)
    if not document:
        return None
    for attr in document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def detect_media_type_telethon(message) -> str | None:
    media = getattr(message, 'media', None)
    if not media:
        return None
    name = media.__class__.__name__.lower()
    return name.replace('message', '')


async def index_channel_post(session: AsyncSession, message: AiogramMessage) -> None:
    existing_channel = await session.scalar(select(Channel).where(Channel.tg_channel_id == message.chat.id))
    if existing_channel is not None and not existing_channel.is_tracked:
        return

    channel = await upsert_channel(
        session=session,
        tg_channel_id=message.chat.id,
        title=message.chat.title or str(message.chat.id),
        username=message.chat.username,
    )

    await upsert_message(
        session=session,
        channel_id=channel.id,
        tg_message_id=message.message_id,
        date=message.date,
        text=message.text,
        caption=message.caption,
        file_name=extract_file_name_aiogram(message),
        media_type=detect_media_type_aiogram(message),
    )
    await session.commit()


async def sync_old_messages(
    config: Config,
    session_factory: async_sessionmaker[AsyncSession],
    identifier: str,
    on_progress: ProgressCallback,
) -> tuple[int, int]:
    if not config.tg_api_id or not config.tg_api_hash:
        raise RuntimeError('TG_API_ID and TG_API_HASH are required for /sync_channel.')

    client = TelegramClient(config.tg_session_name, config.tg_api_id, config.tg_api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError('Telethon session is not authorized. Run a Telethon login once.')

    try:
        entity = await client.get_entity(identifier)
        total = 0
        saved = 0

        async with session_factory() as session:
            channel = await upsert_channel(
                session=session,
                tg_channel_id=telethon_utils.get_peer_id(entity),
                title=getattr(entity, 'title', str(entity.id)),
                username=getattr(entity, 'username', None),
            )

            # Explicitly keep descending order: newest -> oldest.
            async for msg in client.iter_messages(entity, reverse=False):
                total += 1

                if not getattr(msg, 'id', None):
                    continue

                body = getattr(msg, 'message', None)
                media_type = detect_media_type_telethon(msg)
                text = body if not media_type else None
                caption = body if media_type else None
                file_name = extract_file_name_telethon(msg)

                await upsert_message(
                    session=session,
                    channel_id=channel.id,
                    tg_message_id=msg.id,
                    date=getattr(msg, 'date', None),
                    text=text,
                    caption=caption,
                    file_name=file_name,
                    media_type=media_type,
                )
                saved += 1

                if total % 100 == 0:
                    await session.commit()
                    await on_progress(saved, total)

            await session.commit()
            await on_progress(saved, total)

        return saved, total
    finally:
        await client.disconnect()
