"""Схемы полевых отчётов и медиа."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import ReportStatus
from app.schemas.common import Schema


class PhotoRefOut(Schema):
    """Ссылка на загруженное фото.

    `has_geotag` показывается модератору: отчёт без геометки принимается,
    но требует более внимательной проверки — привязать его к участку
    автоматически нельзя.
    """

    id: uuid.UUID
    key: str = Field(validation_alias="storage_key")
    url: str | None = None
    lat: float | None = None
    lon: float | None = None
    taken_at: datetime | None = None
    has_geotag: bool


class ReportCreate(Schema):
    event_id: uuid.UUID
    photo_before_id: uuid.UUID


class ReportUpdate(Schema):
    photo_after_id: uuid.UUID | None = None
    volume_kg: float | None = Field(default=None, ge=0, le=100_000)
    comment: str | None = Field(default=None, max_length=1000)
    submit: bool = Field(
        default=False, description="true — отправить отчёт на модерацию"
    )


class FieldReportOut(Schema):
    id: uuid.UUID
    event_id: uuid.UUID
    segment_id: str
    segment_name_key: str | None = None
    author_name: str = Field(
        description=(
            "Для пользователей 14–17 — только имя, без фамилии: профили "
            "несовершеннолетних закрыты."
        )
    )
    photo_before: PhotoRefOut | None = None
    photo_after: PhotoRefOut | None = None
    volume_kg: float | None = None
    comment: str | None = None
    status: ReportStatus
    submitted_at: datetime | None = None
    moderated_at: datetime | None = None


class ReportDecisionOut(Schema):
    report: FieldReportOut
    hours_awarded: float = 0.0
    attention_index_before: int
    attention_index_after: int
