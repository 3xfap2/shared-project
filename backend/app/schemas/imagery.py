"""Схемы заявок на новую съёмку ДЗЗ."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.models.enums import ImageryPriority, ImageryRequestStatus
from app.schemas.common import Schema


class ImageryRequestCreate(Schema):
    priority: ImageryPriority = ImageryPriority.NORMAL
    comment: str | None = Field(default=None, max_length=1000)


class ImageryRequestOut(Schema):
    id: uuid.UUID
    segment_id: str
    segment_name_key: str | None = None
    oopt_id: str
    priority: ImageryPriority
    status: ImageryRequestStatus
    comment: str | None = None
    expected_at: date | None = Field(
        default=None,
        description="Ожидаемая дата съёмки. Без неё заявка выглядит отправленной в пустоту.",
    )
    reject_reason: str | None = None
    external_id: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime
