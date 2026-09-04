"""Разметка снимков и достижение консенсуса.

Здесь учебное действие волонтёра превращается в рабочий сигнал для
инспектора. Ключевое ограничение: пользователь с ролью `student`
(курс не завершён) сюда не попадает вообще — его разметка не может
повлиять на данные территории.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.deps import ObserverUser, SessionDep
from app.db.base import utcnow
from app.models.annotation import Annotation
from app.models.enums import SegmentStatus
from app.models.geo import Segment
from app.schemas.annotation import (
    AnnotationCreate,
    AnnotationResult,
    AnnotationTaskOut,
)
from app.schemas.geo import SceneOut, SegmentOut
from app.services import attention, calibration, consensus

router = APIRouter(prefix="/annotations", tags=["annotations"])


@router.get(
    "/task",
    response_model=AnnotationTaskOut | None,
    summary="Получить задание на разметку",
)
async def get_task(user: ObserverUser, session: SessionDep):
    """Выдать участок, который пользователь ещё не размечал.

    Порядок выдачи — по убыванию индекса внимания: сначала размечается то,
    что вероятнее всего требует вмешательства. Уже проверенные и чистые
    участки в выдачу не попадают.
    """
    already = select(Annotation.segment_id).where(Annotation.user_id == user.id)

    segment = None

    # Скрытая калибровка: часть заданий — участки с уже известным ответом.
    # Ответ клиенту при этом ничем не отличается от обычного задания —
    # см. пояснение в services/calibration.py.
    if calibration.should_serve_control():
        control_query = (
            select(Segment)
            .where(
                Segment.is_control_pool.is_(True),
                Segment.control_truth.is_not(None),
                Segment.id.notin_(already),
            )
            .options(selectinload(Segment.scenes))
            .order_by(func.random())
            .limit(1)
        )
        segment = (await session.execute(control_query)).scalars().first()

    if segment is None:
        query = (
            select(Segment)
            .where(
                Segment.id.notin_(already),
                Segment.verified.is_(False),
                Segment.status != SegmentStatus.CLEAN,
            )
            .options(selectinload(Segment.scenes))
            .order_by(Segment.attention_index.desc(), Segment.id)
            .limit(1)
        )
        segment = (await session.execute(query)).scalars().first()

    if segment is None or len(segment.scenes) < 2:
        # Разметка возможна только по паре разновременных снимков:
        # мы ищем изменение, а не «мусор на картинке».
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    scenes = sorted(segment.scenes, key=lambda s: s.captured_at)

    # У контрольного участка счётчик разметок давно набран — показ реального
    # числа выдал бы проверку. Показываем нейтральное состояние.
    votes_shown = 0 if segment.is_control_pool else segment.votes

    return AnnotationTaskOut(
        segment_id=segment.id,
        name_key=segment.name_key,
        scene_before=SceneOut.model_validate(scenes[0]),
        scene_after=SceneOut.model_validate(scenes[-1]),
        votes=votes_shown,
        votes_required=segment.votes_required,
    )


@router.post(
    "",
    response_model=AnnotationResult,
    status_code=status.HTTP_201_CREATED,
    summary="Отправить разметку",
)
async def create_annotation(
    payload: AnnotationCreate, user: ObserverUser, session: SessionDep
) -> AnnotationResult:
    """Принять разметку, пересчитать консенсус и индекс участка."""
    segment = await session.get(Segment, payload.segment_id)
    if segment is None:
        raise errors.not_found("segment_not_found")

    is_control = segment.is_control_pool and segment.control_truth is not None
    if segment.verified and not is_control:
        raise errors.conflict("segment_already_verified")

    annotation = Annotation(
        user_id=user.id,
        segment_id=segment.id,
        verdict=payload.verdict,
        marks=[m.model_dump(exclude_none=True) for m in payload.marks],
        # Вес фиксируется на момент отправки: изменение репутации задним
        # числом не должно переписывать уже принятые решения.
        # Учитываем измеренную точность, если её уже достаточно накоплено.
        weight=calibration.blend_reputation(user),
        is_control=is_control,
    )
    session.add(annotation)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Уникальность (user_id, segment_id) держит база: проверка в коде
        # не спасает от двойного клика и параллельных запросов.
        raise errors.conflict("already_annotated") from exc

    if is_control:
        # Контрольное задание: результат идёт в статистику разметчика и
        # НЕ влияет на состояние участка — решение по нему уже принято.
        correct = calibration.check_answer(segment, payload.verdict)
        annotation.was_correct = correct
        calibration.record_result(user, correct=correct)

        user.annotations_count = (
            await session.execute(
                select(func.count()).select_from(Annotation).where(
                    Annotation.user_id == user.id
                )
            )
        ).scalar_one()
        await session.commit()

        # Ответ намеренно неотличим от обычной первой разметки: узнав о
        # проверке, человек начинает работать иначе, и измерение перестаёт
        # отражать его обычную точность. Пользователи предупреждены о
        # контроле качества в условиях использования — это требование
        # добросовестности, а не техническая деталь.
        neutral = SegmentOut.model_validate(segment)
        neutral.verified = False
        neutral.status = SegmentStatus.PROBLEM
        neutral.votes = 1
        return AnnotationResult(
            votes=1,
            votes_required=segment.votes_required,
            consensus_reached=False,
            queued_for_moderation=False,
            segment=neutral,
        )

    rows = await session.execute(
        select(Annotation).where(
            Annotation.segment_id == segment.id,
            Annotation.is_control.is_(False),
        )
    )
    result = consensus.evaluate(rows.scalars().all())

    segment.votes = result.votes
    segment.factor_c = result.factor_c

    queued_now = False
    if result.reached and segment.queued_at is None:
        segment.queued_at = utcnow()
        queued_now = True
        if segment.status == SegmentStatus.WATCH:
            segment.status = SegmentStatus.PROBLEM

    attention.recalculate(segment)

    user.annotations_count = (
        await session.execute(
            select(func.count()).select_from(Annotation).where(
                Annotation.user_id == user.id
            )
        )
    ).scalar_one()

    await session.commit()
    await session.refresh(segment)

    return AnnotationResult(
        votes=result.votes,
        votes_required=result.votes_required,
        consensus_reached=result.reached,
        queued_for_moderation=queued_now or segment.in_moderation_queue,
        segment=SegmentOut.model_validate(segment),
    )
