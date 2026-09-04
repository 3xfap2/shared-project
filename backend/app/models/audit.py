"""Неизменяемый журнал решений.

Зачем он нужен именно здесь: ООПТ — государственная структура, и любое
решение, повлиявшее на данные территории, должно быть прослеживаемым.
Инспектор должен иметь возможность показать, кто, когда и на каком
основании перевёл участок в статус «чистый».

Записи только добавляются. Обновление и удаление не предусмотрены на
уровне приложения; в проде это дополнительно закрепляется правами
пользователя БД (INSERT и SELECT, без UPDATE и DELETE).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey, utcnow
from app.models.enums import AuditAction


class AuditLog(Base, UUIDPrimaryKey):
    __tablename__ = "audit_log"

    # Timestamps сюда не подмешиваем: `updated_at` у неизменяемой записи
    # был бы ложью. Только момент создания.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="NULL, если пользователь удалён — запись журнала переживает аккаунт",
    )
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, native_enum=False, length=30), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Что именно изменилось: {"status": ["problem", "work"], "comment": "..."}
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_actor", "actor_id"),
        Index("ix_audit_created", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"
