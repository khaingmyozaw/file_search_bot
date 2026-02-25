from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, Message


def build_searchable_text(text: str | None, caption: str | None, file_name: str | None) -> str:
    merged = ' '.join(part for part in [text, caption, file_name] if part)
    return merged.lower()


async def upsert_channel(
    session: AsyncSession,
    tg_channel_id: int,
    title: str,
    username: str | None,
) -> Channel:
    stmt = insert(Channel).values(
        tg_channel_id=tg_channel_id,
        title=title,
        username=username,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Channel.tg_channel_id],
        set_={'title': title, 'username': username},
    )
    await session.execute(stmt)
    await session.commit()

    channel = await session.scalar(select(Channel).where(Channel.tg_channel_id == tg_channel_id))
    if channel is None:
        raise RuntimeError('Failed to fetch channel after upsert.')
    return channel


async def find_channel_by_identifier(session: AsyncSession, identifier: str) -> Channel | None:
    token = identifier.strip()
    if not token:
        return None

    if token.startswith('@'):
        token = token[1:]

    if token.lstrip('-').isdigit():
        return await session.scalar(select(Channel).where(Channel.tg_channel_id == int(token)))

    return await session.scalar(select(Channel).where(Channel.username == token))


async def upsert_message(
    session: AsyncSession,
    channel_id: int,
    tg_message_id: int,
    date: datetime | None,
    text: str | None,
    caption: str | None,
    file_name: str | None,
    media_type: str | None,
) -> None:
    searchable_text = build_searchable_text(text, caption, file_name)
    msg_date = date or datetime.now(tz=timezone.utc)

    stmt = insert(Message).values(
        channel_id=channel_id,
        tg_message_id=tg_message_id,
        date=msg_date,
        text=text,
        caption=caption,
        file_name=file_name,
        media_type=media_type,
        searchable_text=searchable_text,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Message.channel_id, Message.tg_message_id],
        set_={
            'date': msg_date,
            'text': text,
            'caption': caption,
            'file_name': file_name,
            'media_type': media_type,
            'searchable_text': searchable_text,
        },
    )
    await session.execute(stmt)


async def search_messages(
    session: AsyncSession,
    keyword: str,
    limit: int,
) -> list[tuple[Message, Channel]]:
    normalized = keyword.lower().strip()
    if not normalized:
        return []

    stmt: Select[tuple[Message, Channel]] = (
        select(Message, Channel)
        .join(Channel, Message.channel_id == Channel.id)
        .where(Message.searchable_text.like(f'%{normalized}%'))
        .order_by(Message.date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.all()


async def channels_with_stats(session: AsyncSession) -> list[tuple[Channel, int]]:
    from sqlalchemy import func

    stmt = (
        select(Channel, func.count(Message.id))
        .outerjoin(Message, Message.channel_id == Channel.id)
        .group_by(Channel.id)
        .order_by(Channel.title.asc())
    )
    result = await session.execute(stmt)
    return result.all()
