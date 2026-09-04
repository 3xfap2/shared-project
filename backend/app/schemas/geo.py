"""Схемы участков, снимков и приоритизации."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import Field, computed_field

from app.models.enums import SceneSource, SegmentStatus
from app.schemas.common import Schema


class FactorsOut(Schema):
    """Факторы индекса внимания.

    Отдаются фронту целиком не для красоты: сотрудник ООПТ должен видеть,
    из чего сложился приоритет. Непрозрачное число он проигнорирует.
    """

    s: float = Field(description="Сигнал ДЗЗ")
    c: float = Field(description="Консенсус людей")
    t: float = Field(description="Давность проверки")
    a: float = Field(description="Доступность участка")


class SceneOut(Schema):
    id: uuid.UUID
    captured_at: date
    source: SceneSource
    resolution_m: float | None = None
    cloud_cover: float | None = None
    tile_url_template: str


class SegmentOut(Schema):
    id: str
    oopt_id: str
    name_key: str = Field(description="Ключ локализации — фронт переводит через i18n")
    length_km: float
    status: SegmentStatus
    attention_index: int = Field(ge=0, le=100)
    votes: int
    votes_required: int
    verified: bool
    last_verified_at: datetime | None = None
    is_subscribed: bool = False

    # Динамика аномалии. Маленькая растущая свалка требует внимания раньше,
    # чем большая стабильная, — фронт показывает это отдельным бейджем.
    is_growing: bool = False
    growth_rate: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def factors(self) -> FactorsOut:
        return FactorsOut(
            s=self.factor_s, c=self.factor_c, t=self.factor_t, a=self.factor_a
        )

    # Плоские поля модели, свёрнутые в `factors` выше.
    factor_s: float = Field(exclude=True)
    factor_c: float = Field(exclude=True)
    factor_t: float = Field(exclude=True)
    factor_a: float = Field(exclude=True)


class HistoryEntry(Schema):
    at: datetime
    event_key: str
    vars: dict[str, Any] | None = None


class SegmentDetailOut(SegmentOut):
    geometry: dict[str, Any] | None = None
    scenes: list[SceneOut] = []
    history: list[HistoryEntry] = []


class PublicStatsOut(Schema):
    segments_watched: int
    segments_cleaned: int
    observers_trained: int


class TrainerTruth(Schema):
    """Эталонная зона мини-тренажёра в долях [0..1]."""

    x: float
    y: float
    radius: float


class TrainerTaskOut(Schema):
    segment_id: str
    scene_before: SceneOut
    scene_after: SceneOut
    truth: TrainerTruth
