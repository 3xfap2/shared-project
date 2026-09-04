"""Разметка снимков волонтёрами."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import Verdict

if TYPE_CHECKING:
    from app.models.geo import Segment
    from app.models.user import User


class Annotation(Base, UUIDPrimaryKey, Timestamps):
    """Одна разметка одного участка одним пользователем.

    Один пользователь — одна разметка на участок. Иначе консенсус
    накручивается повторными отправками, и вся модель доверия рушится.
    Гарантируется уникальным ограничением на уровне БД, а не проверкой
    в коде: при параллельных запросах проверка в коде не спасает.

    `weight` — репутация автора на момент разметки. Фиксируется именно
    здесь, а не берётся из профиля при пересчёте: иначе изменение репутации
    задним числом переписывало бы историю уже принятых решений.
    """

    __tablename__ = "annotations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[Verdict] = mapped_column(
        Enum(Verdict, native_enum=False, length=20), nullable=False
    )

    # Отметки в нормализованных долях тайла [0..1]:
    # [{"x": 0.62, "y": 0.37, "r": 0.06}, ...]
    marks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Снимки, по которым выносился вердикт. Нужны, чтобы при появлении
    # нового снимка отличать свежую разметку от устаревшей.
    scene_before_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )
    scene_after_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )

    # --- Калибровка разметчиков (скрытые эталонные задания) ---
    # Разметка по участку, решение о котором инспектор уже вынес. Волонтёр
    # об этом не знает: как только признак утечёт в интерфейс, механика
    # перестанет измерять реальную точность.
    is_control: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    was_correct: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True,
        doc="Совпал ли вердикт с решением инспектора. Только для контрольных.",
    )

    user: Mapped["User"] = relationship()
    segment: Mapped["Segment"] = relationship(back_populates="annotations")

    __table_args__ = (
        UniqueConstraint("user_id", "segment_id", name="one_annotation_per_user"),
        Index("ix_annotations_segment", "segment_id"),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
    )

    @property
    def indicates_problem(self) -> bool:
        return self.verdict in (Verdict.DUMP, Verdict.LITTER)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Annotation {self.segment_id} {self.verdict}>"
