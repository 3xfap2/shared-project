"""Публичные эндпоинты — сценарий S1, ценность до регистрации.

Здесь человек получает пользу раньше, чем его о чём-то попросили. Это
прямой ответ на барьер входа: первое действие стоит 30 секунд и не требует
аккаунта. Регистрация предлагается после того, как ценность уже получена.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.deps import SessionDep
from app.models.enums import Role, SegmentStatus
from app.models.geo import Segment
from app.models.user import User
from app.schemas.geo import (
    PublicStatsOut,
    SceneOut,
    TrainerTaskOut,
    TrainerTruth,
)

router = APIRouter(prefix="/public", tags=["public"])


@router.get(
    "/trainer/task",
    response_model=TrainerTaskOut,
    summary="Задание мини-тренажёра (без регистрации)",
)
async def trainer_task(session: SessionDep) -> TrainerTaskOut:
    """Пара разновременных снимков и эталонная зона изменения.

    Эталон отдаётся сразу, вместе с заданием: тренажёр обучающий, а не
    оценочный, и второй запрос ради проверки клика только замедлил бы
    первый контакт. Реальная разметка, влияющая на данные ООПТ, работает
    иначе — там эталона нет вовсе.
    """
    segment = (
        await session.execute(
            select(Segment)
            .where(Segment.status == SegmentStatus.PROBLEM)
            .options(selectinload(Segment.scenes))
            .order_by(Segment.attention_index.desc())
            .limit(1)
        )
    ).scalars().first()

    if segment is None or len(segment.scenes) < 2:
        raise errors.not_found("trainer_task_unavailable")

    scenes = sorted(segment.scenes, key=lambda s: s.captured_at)

    # Эталонная зона хранится в геометрии участка как демо-разметка.
    truth = (segment.geometry or {}).get("trainer_truth") or {
        "x": 0.625,
        "y": 0.369,
        "radius": 0.12,
    }

    return TrainerTaskOut(
        segment_id=segment.id,
        scene_before=SceneOut.model_validate(scenes[0]),
        scene_after=SceneOut.model_validate(scenes[-1]),
        truth=TrainerTruth(**truth),
    )


@router.get(
    "/stats", response_model=PublicStatsOut, summary="Публичная статистика"
)
async def public_stats(session: SessionDep) -> PublicStatsOut:
    """Счётчики для лендинга.

    Показываем результат, а не активность: «участков очищено» убедительнее,
    чем «разметок сделано». Персональные данные не раскрываются.
    """
    watched = (
        await session.execute(select(func.count()).select_from(Segment))
    ).scalar_one()

    cleaned = (
        await session.execute(
            select(func.count())
            .select_from(Segment)
            .where(Segment.status == SegmentStatus.CLEAN)
        )
    ).scalar_one()

    trained = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role.in_([Role.OBSERVER, Role.AMBASSADOR]))
        )
    ).scalar_one()

    return PublicStatsOut(
        segments_watched=watched,
        segments_cleaned=cleaned,
        observers_trained=trained,
    )
