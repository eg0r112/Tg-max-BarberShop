from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# (таблица, колонка, DDL-тип и значение по умолчанию) — лёгкие миграции для SQLite
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("services", "category", "VARCHAR(64) NOT NULL DEFAULT 'Другое'"),
    ("bookings", "reminded_day", "BOOLEAN NOT NULL DEFAULT 0"),
    ("bookings", "reminded_2h", "BOOLEAN NOT NULL DEFAULT 0"),
    ("users", "is_blocked", "BOOLEAN NOT NULL DEFAULT 0"),
    ("users", "role_override", "VARCHAR(6)"),
]


def _migrate(sync_conn) -> None:
    insp = inspect(sync_conn)
    tables = set(insp.get_table_names())
    for table, column, ddl in _COLUMN_MIGRATIONS:
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column not in existing:
            logger.info("DB migration: add %s.%s", table, column)
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)
