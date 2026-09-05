"""Полевые отчёты и загруженные фото — контур ground truth."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    UniqueConstraint,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import ReportStatus

if TYPE_CHECKING:
    from app.models.geo import Segment
    from app.models.user import User


class Media(Base, UUIDPrimaryKey, Timestamps):
    """Загруженный файл.

    В базе — только метаданные и ключ в объектном хранилище. Бинарные данные
    в БД не попадают никогда: это раздувает бэкапы и делает репликацию
    неподъёмной.

    Геометка и время съёмки берутся из EXIF, а при их отсутствии передаются
    явно (офлайн-режим PWA, когда камера не записала координаты).
    `has_geotag` фиксирует, был ли источник доверенным: отчёт без геометки
    принимается, но помечается для модератора.
    """

    __tablename__ = "media"

    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    has_geotag: Mapped[bool] = mapped_column(default=False, nullable=False)

    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        Index("ix_media_uploaded_by", "uploaded_by_id"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("lat IS NULL OR (lat >= -90 AND lat <= 90)", name="lat_range"),
        CheckConstraint(
            "lon IS NULL OR (lon >= -180 AND lon <= 180)", name="lon_range"
        ),
    )


class FieldReport(Base, UUIDPrimaryKey, Timestamps):
    """Полевой отчёт: фото «до», фото «после», объём собранного.

    Замыкает цикл системы. Подтверждение отчёта сотрудником ООПТ — это
    единственное событие, которое переводит участок в статус «чистый»
    и обнуляет фактор давности T.

    Фото «до» должно быть сделано до начала уборки: без этого пара
    «до/после» ничего не доказывает. Время съёмки сохраняется, но
    автоматически не проверяется — EXIF приходит от клиента. Гарантию даёт
    модерация: инспектор видит обе фотографии и их метки.
    """

    __tablename__ = "field_reports"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="RESTRICT"), nullable=False
    )

    photo_before_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="RESTRICT"), nullable=False
    )
    photo_after_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="RESTRICT"), nullable=True
    )

    volume_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, native_enum=False, length=20),
        default=ReportStatus.DRAFT,
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    moderated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    moderated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    moderator_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)

    author: Mapped["User"] = relationship(foreign_keys=[user_id], lazy="selectin")
    segment: Mapped["Segment"] = relationship(lazy="selectin")
    photo_before: Mapped["Media"] = relationship(
        foreign_keys=[photo_before_id], lazy="selectin"
    )
    photo_after: Mapped["Media | None"] = relationship(
        foreign_keys=[photo_after_id], lazy="selectin"
    )

    __table_args__ = (
        # Один отчёт на участника и акцию. Без этого один волонтёр сдавал
        # неограниченное число отчётов по одному выезду: каждое
        # подтверждение начисляло часы заново и повторно снижало индекс
        # участка. Накрутка учётного показателя и разрушение
        # приоритизации без единой дополнительной уборки.
        UniqueConstraint("user_id", "event_id", name="one_report_per_event"),
        Index("ix_field_reports_status", "status"),
        Index("ix_field_reports_segment", "segment_id"),
        Index("ix_field_reports_user", "user_id"),
        CheckConstraint(
            "volume_kg IS NULL OR volume_kg >= 0", name="volume_non_negative"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FieldReport {self.segment_id} {self.status}>"
