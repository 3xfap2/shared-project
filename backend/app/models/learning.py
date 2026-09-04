"""Обучающий контур — прогресс по курсу «Школа наблюдателя Земли»."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.user import User


class CourseProgress(Base, UUIDPrimaryKey, Timestamps):
    """Прогресс по одному модулю курса.

    Строка появляется при первой попытке ответа. `completed_at` заполняется
    только при верном ответе — «пройти» модуль неправильным ответом нельзя.

    `attempts` считает все попытки, включая неудачные: это источник метрики
    E4 (точка отсева) из паспорта. Если по одному модулю резко растёт число
    попыток — задание сформулировано плохо.
    """

    __tablename__ = "course_progress"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[str] = mapped_column(String(10), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "module_id", name="one_progress_per_module"),
        Index("ix_course_progress_user", "user_id"),
    )

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CourseProgress {self.module_id} done={self.is_completed}>"
