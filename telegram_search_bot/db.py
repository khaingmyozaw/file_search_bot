from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from models import Base


def _apply_schema_migrations(conn: Connection) -> None:
    if conn.dialect.name != 'sqlite':
        return

    rows = conn.exec_driver_sql('PRAGMA table_info(channels)').fetchall()
    columns = {row[1] for row in rows}
    if 'is_tracked' not in columns:
        conn.exec_driver_sql('ALTER TABLE channels ADD COLUMN is_tracked BOOLEAN NOT NULL DEFAULT 1')


def create_engine_and_session_factory(
    db_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_url, echo=False, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_schema_migrations)
