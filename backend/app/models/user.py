"""Аккаунты и профили пользователей.

Одна таблица `users` держит и учётные данные, и профиль. Разделять их на
`accounts` + `profiles` сейчас нечем оправдать: связь строго 1:1, читаются
они всегда вместе, а лишний JOIN попадал бы в каждый запрос. Разделение
имеет смысл, когда появится второй способ входа (ЕСИА) или несколько
профилей на один аккаунт — тогда это отдельная миграция.

ВОЗРАСТ: храним год рождения, а не возраст.
    Возраст — величина, которая протухает. Пользователь, зарегистрировавшийся
    в 17, через год останется семнадцатилетним в базе, и система будет
    требовать согласие родителя у совершеннолетнего.

    Известное ограничение: год рождения даёт точность ±1 год, потому что
    контракт принимает при регистрации возраст, а не дату рождения. Мы
    сознательно ошибаемся в безопасную сторону — см. `is_minor`.
    Точный фикс — сбор даты рождения; отложен до появления верификации
    личности через ЕСИА.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import Role

if TYPE_CHECKING:
    from app.models.geo import Oopt, Segment


class User(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "users"

    # --- Учётные данные ---
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Профиль ---
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    birth_year: Mapped[int] = mapped_column(Integer, nullable=False)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- Роль и принадлежность ---
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=20),
        default=Role.STUDENT,
        nullable=False,
    )
    oopt_id: Mapped[str | None] = mapped_column(
        ForeignKey("oopts.id", ondelete="SET NULL"), nullable=True
    )

    # --- Накопленные показатели ---
    # Денормализованы сознательно: показываются в профиле при каждом входе,
    # а пересчёт агрегатом по всем разметкам дорожает линейно с ростом базы.
    reputation: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    volunteer_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    annotations_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    certificate_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Результаты скрытых эталонных заданий. Хранятся счётчиками, а не
    # готовой долей: доля выводится, а счётчики позволяют показать объём
    # выборки — «87% на 40 заданиях» и «100% на одном» весят по-разному.
    control_tasks_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    control_correct_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    # --- Связи ---
    oopt: Mapped["Oopt | None"] = relationship(back_populates="staff", lazy="selectin")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Регистр email не должен создавать разных пользователей.
        # Значение приводится к нижнему регистру в сервисном слое,
        # уникальность гарантируется индексом.
        Index("uq_users_email", "email", unique=True),
        Index("ix_users_role", "role"),
        Index("ix_users_oopt_id", "oopt_id"),
        CheckConstraint("reputation >= 0", name="reputation_non_negative"),
        CheckConstraint("volunteer_hours >= 0", name="hours_non_negative"),
    )

    # ---------------------------------------------------------------- #

    @property
    def age(self) -> int:
        """Возраст с точностью ±1 год — см. пояснение в шапке модуля."""
        return date.today().year - self.birth_year

    @property
    def is_minor(self) -> bool:
        """Несовершеннолетний ли пользователь.

        Считаем консервативно: пока не наступил год, в котором пользователю
        гарантированно исполнилось 18, относим его к несовершеннолетним.
        Из-за неточности в ±1 год мы иногда потребуем согласие родителя
        у восемнадцатилетнего. Это осознанный выбор: лишнее согласие —
        неудобство, пропущенное согласие — нарушение.
        """
        return self.age < settings.ADULT_AGE

    @property
    def is_oopt_staff(self) -> bool:
        return self.role in (Role.OOPT_STAFF, Role.ADMIN)

    @property
    def accuracy(self) -> float | None:
        """Доля верных ответов на контрольных заданиях.

        None, пока заданий слишком мало: показывать новичку «0%» после
        одной ошибки — способ его потерять. Порог задан в MIN_CONTROL_TASKS.
        """
        from app.core.config import settings

        if self.control_tasks_count < settings.MIN_CONTROL_TASKS_FOR_ACCURACY:
            return None
        return self.control_correct_count / self.control_tasks_count

    def can_annotate(self) -> bool:
        """Реальная разметка доступна только после завершения курса."""
        return self.role in (Role.OBSERVER, Role.AMBASSADOR, Role.ADMIN)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email} role={self.role}>"


class Subscription(Base, Timestamps):
    """Подписка волонтёра на участок.

    Механика повторного участия: при появлении нового снимка подписчик
    получает уведомление. Составной первичный ключ — подписка не сущность
    со своей жизнью, а факт связи.
    """

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"),
        primary_key=True,
    )

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    segment: Mapped["Segment"] = relationship(back_populates="subscribers")
