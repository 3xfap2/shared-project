"""Схемы разметки и модерации."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.models.enums import ModerationDecision, Verdict
from app.schemas.common import Schema
from app.schemas.geo import SceneOut, SegmentOut


class Mark(Schema):
    """Отметка в нормализованных долях тайла.

    Доли, а не пиксели: клиент показывает снимок в произвольном размере,
    и координаты в пикселях зависели бы от вёрстки и экрана.
    """

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    r: float | None = Field(default=None, ge=0, le=1)


class AnnotationTaskOut(Schema):
    segment_id: str
    name_key: str
    scene_before: SceneOut
    scene_after: SceneOut
    votes: int
    votes_required: int


class AnnotationCreate(Schema):
    segment_id: str
    verdict: Verdict
    marks: list[Mark] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _marks_required_for_problem(self) -> "AnnotationCreate":
        """Вердикт «есть проблема» без указания места бесполезен —
        инспектор не поймёт, куда смотреть."""
        if self.verdict != Verdict.NONE and not self.marks:
            raise ValueError("marks_required_for_problem")
        return self


class AnnotationResult(Schema):
    votes: int
    votes_required: int
    consensus_reached: bool
    queued_for_moderation: bool
    segment: SegmentOut


class ModerationItemOut(Schema):
    segment: SegmentOut
    annotations_count: int
    agreement: float = Field(ge=0, le=1, description="Доля согласия разметчиков")
    marks_heatmap: list[Mark] = []
    queued_at: datetime | None = None
    annotators_accuracy: float | None = Field(
        default=None,
        description=(
            "Средняя измеренная точность разметчиков этого участка. "
            "Ради неё и делались контрольные задания: инспектор видит не "
            "«трое что-то отметили», а «трое с точностью 87%»."
        ),
    )


class ModerationDecisionIn(Schema):
    decision: ModerationDecision
    comment: str | None = Field(default=None, max_length=500)


class BatchModerationIn(Schema):
    """Пакетное решение по нескольким однотипным участкам.

    Инспектор ООПТ разбирает очередь пачками: соседние участки одного
    берега после одного шторма выглядят одинаково, и требовать по клику
    на каждый — прямой путь к тому, что систему перестанут открывать.
    Ограничение в 50 участков не даёт случайно подтвердить всю очередь.
    """

    segment_ids: list[str] = Field(min_length=1, max_length=50)
    decision: ModerationDecision
    comment: str | None = Field(default=None, max_length=500)


class BatchModerationResult(Schema):
    applied: list[str] = Field(description="Участки, по которым решение принято")
    skipped: dict[str, str] = Field(
        default_factory=dict,
        description="Участок → код причины пропуска (чужая территория, не в очереди)",
    )


class DigestOut(Schema):
    """Сезонная сводка по территории для сотрудника ООПТ.

    Механика удержания второй аудитории: инспектор должен получать от
    системы не только задачи, но и результат — иначе она остаётся для него
    источником работы, а не пользы.
    """

    period_from: datetime
    period_to: datetime
    segments_total: int
    segments_confirmed: int
    segments_cleaned: int
    annotations_received: int
    volunteers_active: int
    volume_kg_total: float
    median_moderation_hours: float | None = Field(
        default=None, description="Медиана времени от консенсуса до решения (KPI Q3)"
    )
    top_segments: list[SegmentOut] = Field(
        default_factory=list, description="Участки с наибольшим индексом внимания"
    )
