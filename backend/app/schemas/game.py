"""Схемы игрового контура."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import DuelStatus, ModerationDecision, Verdict
from app.schemas.annotation import Mark
from app.schemas.common import Schema
from app.schemas.geo import SceneOut


# ------------------------------------------------------------------ #
# Дуэль разметчиков
# ------------------------------------------------------------------ #

class DuelOut(Schema):
    """Состояние дуэли для игрока.

    Ход соперника не раскрывается, пока игрок не сходил сам: иначе игра
    превращается в списывание, а разметка теряет независимость — и вместе
    с ней ценность для консенсуса.
    """

    id: uuid.UUID
    status: DuelStatus
    segment_id: str
    name_key: str
    scene_before: SceneOut | None = None
    scene_after: SceneOut | None = None

    my_move_done: bool = False
    opponent_joined: bool = False
    opponent_move_done: bool = False

    my_score: int | None = None
    opponent_score: int | None = None
    agreed: bool | None = Field(
        default=None, description="Совпали ли вердикты. Заполняется после обоих ходов"
    )
    result: str | None = Field(
        default=None, description="win | lose | draw — после завершения дуэли"
    )
    finished_at: datetime | None = None


class DuelMove(Schema):
    verdict: Verdict
    marks: list[Mark] = Field(default_factory=list, max_length=20)
    elapsed_ms: int = Field(
        ge=0,
        le=60 * 60 * 1000,
        description=(
            "Время хода по замеру клиента. Влияет только на бонус за "
            "скорость и не может дать больше очков, чем согласие с соперником"
        ),
    )


# ------------------------------------------------------------------ #
# «Инспектор на день»
# ------------------------------------------------------------------ #

class InspectorCaseOut(Schema):
    """Карточка на разбор. Настоящее решение инспектора не раскрывается."""

    segment_id: str
    name_key: str
    scene_before: SceneOut | None = None
    scene_after: SceneOut | None = None
    annotations_count: int
    agreement: float = Field(ge=0, le=1)
    marks_heatmap: list[Mark] = []
    attention_index: int


class InspectorAnswer(Schema):
    segment_id: str
    decision: ModerationDecision


class InspectorResultOut(Schema):
    correct: bool
    player_decision: ModerationDecision
    actual_decision: ModerationDecision
    points: int
    explanation_key: str = Field(
        description="Ключ пояснения, почему инспектор решил именно так"
    )
    accuracy: float | None = None
    rounds_played: int


# ------------------------------------------------------------------ #
# Общее
# ------------------------------------------------------------------ #

class GameStatsOut(Schema):
    duels_played: int
    duels_won: int
    duel_points: int
    inspector_rounds: int
    inspector_correct: int
    inspector_accuracy: float | None = None
    total_points: int


class LeaderboardEntry(Schema):
    rank: int
    display_name: str = Field(
        description=(
            "Для пользователей 14–17 — псевдоним: профили несовершеннолетних "
            "закрыты, и рейтинг не повод их раскрывать"
        )
    )
    points: int
    duels_won: int


class LeaderboardOut(Schema):
    entries: list[LeaderboardEntry]
    my_rank: int | None = None
    my_points: int = 0
