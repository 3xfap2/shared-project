"""Заявки на новую съёмку ДЗЗ.

Делает связь с космическими данными двусторонней. Обычно платформа только
потребляет снимки; здесь инспектор ООПТ может запросить съёмку конкретного
участка — Геопортал Роскосмоса принимает такие заявки штатно.

Ценность в том, что мониторинг перестаёт зависеть от того, когда спутник
случайно пролетит над нужным местом: territoria получает снимок тогда,
когда он ей нужен.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import ImageryPriority, ImageryRequestStatus

if TYPE_CHECKING:
    from app.models.geo import Segment
    from app.models.user import User


class ImageryRequest(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "imagery_requests"

    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False
    )
    oopt_id: Mapped[str] = mapped_column(
        ForeignKey("oopts.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    priority: Mapped[ImageryPriority] = mapped_column(
        Enum(ImageryPriority, native_enum=False, length=20),
        default=ImageryPriority.NORMAL,
        nullable=False,
    )
    status: Mapped[ImageryRequestStatus] = mapped_column(
        Enum(ImageryRequestStatus, native_enum=False, length=20),
        default=ImageryRequestStatus.QUEUED,
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ожидаемая дата съёмки, которую называет оператор. Показывается
    # инспектору: без неё заявка выглядит как отправленная в пустоту.
    expected_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Идентификатор заявки на стороне оператора ДЗЗ — для сверки.
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    segment: Mapped["Segment"] = relationship(lazy="selectin")
    requested_by: Mapped["User | None"] = relationship(lazy="selectin")

    __table_args__ = (
        Index("ix_imagery_requests_segment", "segment_id"),
        Index("ix_imagery_requests_status", "status"),
        Index("ix_imagery_requests_oopt", "oopt_id"),
    )

    @property
    def is_active(self) -> bool:
        """Активная заявка блокирует повторную по тому же участку:
        две заявки на одну съёмку — лишние деньги и путаница в очереди."""
        return self.status in (
            ImageryRequestStatus.QUEUED,
            ImageryRequestStatus.ACCEPTED,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ImageryRequest {self.segment_id} {self.status}>"
