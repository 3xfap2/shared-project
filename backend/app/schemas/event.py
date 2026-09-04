"""Схемы акций, записи и допуска."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import (
    ConsentStatus,
    EnrollmentStatus,
    EventStatus,
    Requirement,
)
from app.schemas.common import Schema


class EventCreate(Schema):
    segment_id: str
    starts_at: datetime
    capacity: int = Field(ge=1, le=500, examples=[25])
    meeting_point: str | None = Field(default=None, max_length=300)


class EventOut(Schema):
    id: uuid.UUID
    segment_id: str
    segment_name_key: str
    oopt_id: str
    starts_at: datetime
    capacity: int
    enrolled_count: int
    meeting_point: str | None = None
    status: EventStatus
    is_my_segment: bool = Field(
        default=False,
        description=(
            "Размечал ли текущий пользователь этот участок. На этом держится "
            "механика персональной привязки — ключевой переход «онлайн → поле»."
        ),
    )


class EnrollmentOut(Schema):
    event_id: uuid.UUID
    status: EnrollmentStatus
    blocking_requirements: list[Requirement] = Field(
        description="Что осталось выполнить для допуска. Пустой список — допущен."
    )
    consent_status: ConsentStatus | None = None
    briefing_completed_at: datetime | None = None


class ConsentRequest(Schema):
    parent_contact: str = Field(
        min_length=3,
        max_length=255,
        description="E-mail или телефон законного представителя",
    )
