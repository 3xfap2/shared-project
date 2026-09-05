"""Базовый класс моделей и общие примитивы.

РЕШЕНИЕ О ГЕОМЕТРИИ (важно для будущего масштабирования)

Геометрия участков хранится как GeoJSON в JSON-колонке, а не в колонке
PostGIS `geometry`. Причина: в контракте v1 нет ни одного пространственного
запроса — участки фильтруются по территории и сортируются по индексу внимания.
JSON работает и на PostgreSQL, и на SQLite, поэтому тесты запускаются без
поднятого Docker.

PostGIS появляется в схеме тогда, когда появится первый реальный
пространственный запрос — привязка фото по геометке к ближайшему участку
(`ST_DWithin`) или выборка по bbox карты. Это будет отдельная миграция:
добавление колонки `geom geometry(Geometry, 4326)`, заполнение её из
существующего GeoJSON и GiST-индекс. Данные при этом не теряются.

Так мы не платим за инфраструктуру, которая пока не нужна, и не блокируем
её появление позже.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Всегда timezone-aware. Наивных datetime в проекте нет."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Привести datetime к timezone-aware UTC.

    Зачем это нужно. SQLite не хранит часовой пояс и возвращает наивные
    datetime даже для колонок `DateTime(timezone=True)`. PostgreSQL их
    возвращает с зоной. Значит, один и тот же код сравнения времени
    работает в проде и падает в тестах — или наоборот.

    Наивное значение трактуем как UTC: всё, что мы записываем, пишется
    через `utcnow()`, других источников времени в базе нет.

    Применять ко всякому времени, пришедшему из базы или от клиента,
    перед сравнением с `utcnow()`.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Общий базовый класс. Метаданные именуются по соглашению, чтобы
    Alembic генерировал стабильные имена индексов и ограничений."""

    # Соглашение об именах передаётся в MetaData. Раньше словарь лежал в
    # атрибуте `metadata_naming_convention`, которого SQLAlchemy не знает:
    # он просто игнорировался, и заявленные стабильные имена не работали.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class UUIDPrimaryKey:
    """UUID вместо автоинкремента.

    Идентификаторы не раскрывают порядок и объём данных, их можно генерировать
    на стороне приложения, и они не конфликтуют при будущем шардировании
    или слиянии баз нескольких регионов.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
