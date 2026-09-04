"""Подключение к базе и выдача сессий.

Асинхронный движок: обработка ДЗЗ и уведомления — операции с ожиданием
ввода-вывода, и синхронный стек упирался бы в пул потоков.

SQLite используется для тестов и локальной разработки без Docker.
У него по умолчанию отключены внешние ключи, поэтому включаем их явно —
иначе тесты не поймают нарушение целостности, которое поймает PostgreSQL.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    future=True,
)

if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Зависимость FastAPI: сессия на запрос.

    Коммит выполняет обработчик — это осознанно. Автокоммит на выходе
    скрывал бы момент фиксации и мешал бы обработчикам, которым нужно
    несколько шагов в одной транзакции.
    """
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
