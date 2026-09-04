"""Запись решений в неизменяемый журнал."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.enums import AuditAction


def record(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: str,
    actor_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Добавить запись в журнал.

    Не коммитит: запись должна попасть в ту же транзакцию, что и само
    решение. Иначе возможна ситуация «участок подтверждён, но в журнале
    этого нет» — ровно то, чего журнал и должен не допускать.
    """
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        actor_id=actor_id,
        payload=payload,
    )
    session.add(entry)
    return entry
