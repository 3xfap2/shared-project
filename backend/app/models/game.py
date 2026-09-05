"""Игровой контур.

ПРИНЦИП, ОТЛИЧАЮЩИЙ ЭТИ ИГРЫ ОТ РАЗВЛЕЧЕНИЯ. Игра не приделана сбоку —
она даёт соревновательную форму тому действию, которое и так составляет
суть продукта. Играя, человек производит настоящие данные:

  * «Дуэль разметчиков» — оба игрока размечают реальный участок, и обе
    разметки уходят в тот же консенсус, что и обычные. Два независимых
    мнения — это уже две трети порога.
  * «Инспектор на день» — игрок разбирает случаи с уже известным решением
    инспектора. Новых данных не создаёт, но учит логике второй аудитории
    и даёт нам меру того, насколько человек понимает критерии проверки.

Приём заимствован у Zooniverse: там на миллиарде разметок доказано, что
соревновательная подача не портит качество, если результат всё равно
проходит консенсус и проверку эксперта.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import DuelStatus, ModerationDecision, Verdict

if TYPE_CHECKING:
    from app.models.geo import Segment
    from app.models.user import User


class Duel(Base, UUIDPrimaryKey, Timestamps):
    """Дуэль двух разметчиков на одном участке.

    Подбор асинхронный, а не в реальном времени. Причина практическая:
    вебсокеты и одновременное присутствие двух игроков означали бы, что
    игра работает только в час пик. Асинхронная дуэль ждёт соперника
    столько, сколько нужно, и работает при любой посещаемости.
    """

    __tablename__ = "duels"

    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[DuelStatus] = mapped_column(
        Enum(DuelStatus, native_enum=False, length=20),
        default=DuelStatus.OPEN,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    segment: Mapped["Segment"] = relationship(lazy="selectin")
    entries: Mapped[list["DuelEntry"]] = relationship(
        back_populates="duel", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_duels_status", "status"),
        Index("ix_duels_segment", "segment_id"),
    )

    @property
    def is_full(self) -> bool:
        return len(self.entries) >= 2

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Duel {self.segment_id} {self.status}>"


class DuelEntry(Base, UUIDPrimaryKey, Timestamps):
    """Ход одного игрока в дуэли."""

    __tablename__ = "duel_entries"

    duel_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("duels.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    verdict: Mapped[Verdict | None] = mapped_column(
        Enum(Verdict, native_enum=False, length=20), nullable=True
    )
    marks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    # Время в миллисекундах меряет клиент. Доверять ему нельзя: значение
    # легко занизить. Поэтому скорость даёт лишь часть очков, а верхняя
    # граница отсекает заведомо невозможные результаты.
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    duel: Mapped["Duel"] = relationship(back_populates="entries")
    user: Mapped["User"] = relationship(lazy="selectin")

    __table_args__ = (
        # Один игрок — один ход. Иначе дуэль накручивается повторной отправкой.
        UniqueConstraint("duel_id", "user_id", name="one_entry_per_duel"),
        Index("ix_duel_entries_user", "user_id"),
        CheckConstraint("score >= 0", name="score_non_negative"),
        CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0", name="elapsed_non_negative"
        ),
    )

    @property
    def is_submitted(self) -> bool:
        return self.submitted_at is not None


class InspectorRound(Base, UUIDPrimaryKey, Timestamps):
    """Раунд игры «Инспектор на день».

    Игроку показывается карточка модерации по участку, решение о котором
    инспектор уже вынес, и он решает сам. Сразу после ответа открывается
    настоящее решение с комментарием.

    Зачем это продукту, а не только развлечению: молодёжь перестаёт
    воспринимать инспектора как абстрактную инстанцию, которая «почему-то
    отклонила разметку». Понимание критериев проверки — самый дешёвый
    способ поднять качество разметки.

    На состояние участка раунд не влияет никак: решение по нему принято.
    """

    __tablename__ = "inspector_rounds"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False
    )

    player_decision: Mapped[ModerationDecision] = mapped_column(
        Enum(ModerationDecision, native_enum=False, length=20), nullable=False
    )
    actual_decision: Mapped[ModerationDecision] = mapped_column(
        Enum(ModerationDecision, native_enum=False, length=20), nullable=False
    )
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)

    segment: Mapped["Segment"] = relationship(lazy="selectin")

    __table_args__ = (
        # Один разбор на участок: иначе игрок переигрывает тот же случай
        # и статистика перестаёт что-либо значить.
        UniqueConstraint("user_id", "segment_id", name="one_round_per_segment"),
        Index("ix_inspector_rounds_user", "user_id"),
    )
