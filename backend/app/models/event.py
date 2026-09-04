"""Акции, запись волонтёров и допуск.

Контур допуска — самая ответственная часть системы: здесь несовершеннолетние
попадают на охраняемую территорию. Все три условия (курс завершён, согласие
родителя получено, инструктаж пройден) проверяются на сервере и не могут
быть обойдены с клиента.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import ConsentStatus, EnrollmentStatus, EventStatus

if TYPE_CHECKING:
    from app.models.geo import Segment
    from app.models.user import User


class Event(Base, UUIDPrimaryKey, Timestamps):
    """Акция (экспедиция) на участке.

    Создаётся только сотрудником ООПТ. Волонтёр не может назначить выход
    на охраняемую территорию — это требование режима ООПТ, а не продуктовое
    ограничение. Амбассадор подаёт заявку, которую сотрудник подтверждает.
    """

    __tablename__ = "events"

    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="RESTRICT"), nullable=False
    )
    oopt_id: Mapped[str] = mapped_column(
        ForeignKey("oopts.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    meeting_point: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, native_enum=False, length=20),
        default=EventStatus.PLANNED,
        nullable=False,
    )

    segment: Mapped["Segment"] = relationship(lazy="selectin")
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_events_segment", "segment_id"),
        Index("ix_events_starts_at", "starts_at"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.segment_id} {self.starts_at:%Y-%m-%d}>"


class Enrollment(Base, UUIDPrimaryKey, Timestamps):
    """Запись волонтёра на акцию и состояние его допуска.

    Статус не хранится «на веру»: он выводится из фактического состояния
    условий — см. `services.enrollment.evaluate_requirements()`. Поле
    `status` держится денормализованно только для выборок и обновляется
    там же, где пересчитываются условия.
    """

    __tablename__ = "enrollments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(EnrollmentStatus, native_enum=False, length=30),
        default=EnrollmentStatus.PENDING_REQUIREMENTS,
        nullable=False,
    )
    consent_status: Mapped[ConsentStatus] = mapped_column(
        Enum(ConsentStatus, native_enum=False, length=20),
        default=ConsentStatus.NOT_REQUIRED,
        nullable=False,
    )
    briefing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(lazy="selectin")
    event: Mapped["Event"] = relationship(back_populates="enrollments")
    consent: Mapped["Consent | None"] = relationship(
        back_populates="enrollment",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        # Повторная запись на ту же акцию невозможна на уровне БД:
        # проверка в коде не защищает от гонки при двойном клике.
        UniqueConstraint("user_id", "event_id", name="one_enrollment_per_event"),
        Index("ix_enrollments_event", "event_id"),
    )

    @property
    def is_briefed(self) -> bool:
        return self.briefing_completed_at is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Enrollment {self.user_id} {self.status}>"


class Consent(Base, UUIDPrimaryKey, Timestamps):
    """Согласие законного представителя на участие несовершеннолетнего.

    Хранится отдельной сущностью, а не флагом в записи: это юридически
    значимый документ, у него есть адресат, момент подписания и срок
    хранения. Отдельная таблица позволяет удалять согласия по истечении
    срока, не трогая историю участия.

    Контакт родителя — персональные данные третьего лица, поэтому доступ
    к нему имеет только владелец записи и администратор; в API он не
    возвращается никогда.
    """

    __tablename__ = "consents"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    parent_contact: Mapped[str] = mapped_column(String(255), nullable=False)

    # Одноразовый токен для ссылки подписания.
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    enrollment: Mapped["Enrollment"] = relationship(back_populates="consent")

    __table_args__ = (Index("ix_consents_token", "token"),)

    @property
    def is_signed(self) -> bool:
        return self.signed_at is not None
