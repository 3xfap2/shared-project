"""Игровой контур: дуэль разметчиков и «Инспектор на день».

Обе игры доступны только роли `observer` и выше. Причина та же, что и у
обычной разметки: пока курс не пройден, человек не понимает, что ищет,
и его ход не имеет ценности ни для игры, ни для системы.

Дуэль производит настоящую разметку — она уходит в общий консенсус.
«Инспектор на день» новых данных не создаёт: там разбираются случаи,
решение по которым уже принято.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.deps import ObserverUser, SessionDep
from app.db.base import utcnow
from app.models.annotation import Annotation
from app.models.enums import DuelStatus, ModerationDecision, SegmentStatus, Verdict
from app.models.game import Duel, DuelEntry, InspectorRound
from app.models.geo import Segment
from app.models.user import User
from app.schemas.annotation import Mark
from app.schemas.game import (
    DuelMove,
    DuelOut,
    GameStatsOut,
    InspectorAnswer,
    InspectorCaseOut,
    InspectorResultOut,
    LeaderboardEntry,
    LeaderboardOut,
)
from app.schemas.geo import SceneOut
from app.services import attention, calibration, consensus, games

router = APIRouter(prefix="/games", tags=["games"])


def _display_name(user: User) -> str:
    """Псевдоним для несовершеннолетних: рейтинг не повод раскрывать профиль."""
    return user.name if not user.is_minor else f"Наблюдатель #{str(user.id)[-4:].upper()}"


def _scenes(segment: Segment) -> tuple[SceneOut | None, SceneOut | None]:
    ordered = sorted(segment.scenes, key=lambda s: s.captured_at)
    if len(ordered) < 2:
        return None, None
    return SceneOut.model_validate(ordered[0]), SceneOut.model_validate(ordered[-1])


def _duel_out(duel: Duel, user_id: uuid.UUID) -> DuelOut:
    mine = next((e for e in duel.entries if e.user_id == user_id), None)
    other = next((e for e in duel.entries if e.user_id != user_id), None)

    before, after = _scenes(duel.segment) if duel.segment else (None, None)
    finished = duel.status is DuelStatus.FINISHED

    result = None
    if finished and mine is not None:
        if other is None or mine.score == (other.score if other else 0):
            result = "draw"
        else:
            result = "win" if mine.is_winner else "lose"

    return DuelOut(
        id=duel.id,
        status=duel.status,
        segment_id=duel.segment_id,
        name_key=duel.segment.name_key if duel.segment else "",
        scene_before=before,
        scene_after=after,
        my_move_done=mine is not None and mine.is_submitted,
        opponent_joined=other is not None,
        opponent_move_done=other is not None and other.is_submitted,
        # Очки соперника показываем только после завершения: до этого они
        # раскрыли бы его ход, а разметки обязаны быть независимыми.
        my_score=mine.score if finished and mine else None,
        opponent_score=other.score if finished and other else None,
        agreed=None if not finished else _agreed(duel),
        result=result,
        finished_at=duel.finished_at,
    )


def _agreed(duel: Duel) -> bool:
    verdicts = [e.verdict for e in duel.entries if e.verdict is not None]
    if len(verdicts) < 2:
        return False
    problem = [v in (Verdict.DUMP, Verdict.LITTER) for v in verdicts]
    return problem[0] == problem[1]


# ================================================================== #
# Дуэль разметчиков
# ================================================================== #

@router.post(
    "/duels/join",
    response_model=DuelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Найти соперника или создать дуэль",
)
async def join_duel(user: ObserverUser, session: SessionDep) -> DuelOut:
    """Подобрать дуэль.

    Сначала ищем открытую дуэль другого игрока — так подбор идёт быстрее
    и участок получает второе независимое мнение. Если открытых нет,
    создаём свою и ждём соперника.

    Участок берётся из тех, что игрок ещё не размечал: иначе он вспомнит
    собственный прошлый ответ, и независимости не будет.
    """
    already = select(Annotation.segment_id).where(Annotation.user_id == user.id)
    my_duels = select(DuelEntry.duel_id).where(DuelEntry.user_id == user.id)

    open_duel = (
        await session.execute(
            select(Duel)
            .where(
                Duel.status == DuelStatus.OPEN,
                Duel.id.notin_(my_duels),
                Duel.segment_id.notin_(already),
            )
            .options(selectinload(Duel.entries), selectinload(Duel.segment))
            .order_by(Duel.created_at)
            .limit(1)
        )
    ).scalars().first()

    if open_duel is not None:
        duel = open_duel
        duel.status = DuelStatus.ACTIVE
    else:
        segment = (
            await session.execute(
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
        ).scalars().first()

        if segment is None or len(segment.scenes) < 2:
            raise errors.not_found("no_duel_segment_available")

        duel = Duel(segment_id=segment.id, status=DuelStatus.OPEN)
        session.add(duel)
        await session.flush()
        duel.segment = segment

    session.add(DuelEntry(duel_id=duel.id, user_id=user.id))

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise errors.conflict("already_in_duel") from exc

    refreshed = (
        await session.execute(
            select(Duel)
            .where(Duel.id == duel.id)
            .options(
                selectinload(Duel.entries),
                selectinload(Duel.segment).selectinload(Segment.scenes),
            )
        )
    ).scalar_one()
    return _duel_out(refreshed, user.id)


@router.post(
    "/duels/{duel_id}/move", response_model=DuelOut, summary="Сделать ход"
)
async def make_move(
    duel_id: uuid.UUID,
    payload: DuelMove,
    user: ObserverUser,
    session: SessionDep,
) -> DuelOut:
    """Отправить вердикт.

    Ход одновременно является обычной разметкой: она уходит в тот же
    консенсус, что и разметка вне игры. Игра не создаёт параллельного
    контура данных — иначе пришлось бы доверять игровым ответам меньше,
    и весь смысл терялся бы.
    """
    duel = (
        await session.execute(
            select(Duel)
            .where(Duel.id == duel_id)
            .options(
                selectinload(Duel.entries),
                selectinload(Duel.segment).selectinload(Segment.scenes),
            )
        )
    ).scalar_one_or_none()
    if duel is None:
        raise errors.not_found("duel_not_found")
    if duel.status is DuelStatus.FINISHED:
        raise errors.conflict("duel_already_finished")

    entry = next((e for e in duel.entries if e.user_id == user.id), None)
    if entry is None:
        raise errors.forbidden("not_a_duel_participant")
    if entry.is_submitted:
        raise errors.conflict("move_already_made")

    if payload.verdict != Verdict.NONE and not payload.marks:
        raise errors.unprocessable("marks_required_for_problem")

    marks = [m.model_dump(exclude_none=True) for m in payload.marks]
    entry.verdict = payload.verdict
    entry.marks = marks
    entry.elapsed_ms = payload.elapsed_ms
    entry.submitted_at = utcnow()

    # Ход становится настоящей разметкой участка. Дубль возможен, если
    # игрок уже размечал участок вне игры — тогда игровой ход просто
    # не попадает в консенсус второй раз.
    existing = (
        await session.execute(
            select(Annotation).where(
                Annotation.user_id == user.id,
                Annotation.segment_id == duel.segment_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            Annotation(
                user_id=user.id,
                segment_id=duel.segment_id,
                verdict=payload.verdict,
                marks=marks,
                weight=calibration.blend_reputation(user),
            )
        )
        await session.flush()

        rows = await session.execute(
            select(Annotation).where(
                Annotation.segment_id == duel.segment_id,
                Annotation.is_control.is_(False),
            )
        )
        result = consensus.evaluate(rows.scalars().all())
        segment = duel.segment
        segment.votes = result.votes
        segment.factor_c = result.factor_c
        if result.reached and segment.queued_at is None:
            segment.queued_at = utcnow()
            if segment.status == SegmentStatus.WATCH:
                segment.status = SegmentStatus.PROBLEM
        attention.recalculate(segment)

    # Оба сходили — считаем очки.
    entries = sorted(duel.entries, key=lambda e: e.created_at)
    if len(entries) == 2 and all(e.is_submitted for e in entries):
        a, b = entries
        outcome = games.score_duel(
            a.verdict.value if a.verdict else None, a.elapsed_ms,
            b.verdict.value if b.verdict else None, b.elapsed_ms,
        )
        a.score, b.score = outcome.score_a, outcome.score_b
        a.is_winner = outcome.winner_index == 0
        b.is_winner = outcome.winner_index == 1
        duel.status = DuelStatus.FINISHED
        duel.finished_at = utcnow()

    await session.commit()

    refreshed = (
        await session.execute(
            select(Duel)
            .where(Duel.id == duel_id)
            .options(
                selectinload(Duel.entries),
                selectinload(Duel.segment).selectinload(Segment.scenes),
            )
        )
    ).scalar_one()
    return _duel_out(refreshed, user.id)


@router.get("/duels/{duel_id}", response_model=DuelOut, summary="Состояние дуэли")
async def get_duel(
    duel_id: uuid.UUID, user: ObserverUser, session: SessionDep
) -> DuelOut:
    duel = (
        await session.execute(
            select(Duel)
            .where(Duel.id == duel_id)
            .options(
                selectinload(Duel.entries),
                selectinload(Duel.segment).selectinload(Segment.scenes),
            )
        )
    ).scalar_one_or_none()
    if duel is None:
        raise errors.not_found("duel_not_found")
    if not any(e.user_id == user.id for e in duel.entries):
        raise errors.forbidden("not_a_duel_participant")
    return _duel_out(duel, user.id)


# ================================================================== #
# «Инспектор на день»
# ================================================================== #

@router.get(
    "/inspector/case",
    response_model=InspectorCaseOut,
    summary="Случай на разбор",
)
async def inspector_case(user: ObserverUser, session: SessionDep) -> InspectorCaseOut:
    """Выдать участок с уже вынесенным решением инспектора.

    Настоящее решение не раскрывается — оно откроется после ответа.
    """
    played = select(InspectorRound.segment_id).where(
        InspectorRound.user_id == user.id
    )

    segment = (
        await session.execute(
            select(Segment)
            .where(
                Segment.is_control_pool.is_(True),
                Segment.control_truth.is_not(None),
                Segment.id.notin_(played),
            )
            .options(selectinload(Segment.scenes), selectinload(Segment.annotations))
            .order_by(func.random())
            .limit(1)
        )
    ).scalars().first()

    if segment is None:
        raise errors.not_found("no_inspector_case_available")

    before, after = _scenes(segment)
    result = consensus.evaluate(
        [a for a in segment.annotations if not a.is_control]
    )
    heatmap = [
        Mark(**mark)
        for a in segment.annotations
        for mark in (a.marks or [])
    ][:100]

    return InspectorCaseOut(
        segment_id=segment.id,
        name_key=segment.name_key,
        scene_before=before,
        scene_after=after,
        annotations_count=result.total,
        agreement=result.agreement,
        marks_heatmap=heatmap,
        attention_index=segment.attention_index,
    )


@router.post(
    "/inspector/answer",
    response_model=InspectorResultOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ответ игрока и настоящее решение инспектора",
)
async def inspector_answer(
    payload: InspectorAnswer, user: ObserverUser, session: SessionDep
) -> InspectorResultOut:
    segment = await session.get(Segment, payload.segment_id)
    if segment is None or segment.control_truth is None:
        raise errors.not_found("segment_not_found")

    # Эталон хранится вердиктом: «проблема есть» означает, что инспектор
    # разметку подтвердил, «проблемы нет» — что отклонил.
    actual = (
        ModerationDecision.APPROVE
        if segment.control_truth != Verdict.NONE
        else ModerationDecision.REJECT
    )
    correct = payload.decision == actual

    session.add(
        InspectorRound(
            user_id=user.id,
            segment_id=segment.id,
            player_decision=payload.decision,
            actual_decision=actual,
            is_correct=correct,
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise errors.conflict("case_already_played") from exc

    rounds = (
        await session.execute(
            select(
                func.count(InspectorRound.id),
                func.sum(func.cast(InspectorRound.is_correct, Integer)),
            ).where(InspectorRound.user_id == user.id)
        )
    ).one()
    total = rounds[0] or 0
    correct_total = int(rounds[1] or 0)

    return InspectorResultOut(
        correct=correct,
        player_decision=payload.decision,
        actual_decision=actual,
        points=games.score_inspector_round(correct=correct),
        explanation_key=f"game.inspector.why.{actual.value}",
        accuracy=games.inspector_accuracy(correct_total, total),
        rounds_played=total,
    )


# ================================================================== #
# Статистика и рейтинг
# ================================================================== #

@router.get("/stats", response_model=GameStatsOut, summary="Моя игровая статистика")
async def my_stats(user: ObserverUser, session: SessionDep) -> GameStatsOut:
    duel_rows = (
        await session.execute(
            select(
                func.count(DuelEntry.id),
                func.coalesce(func.sum(DuelEntry.score), 0),
            ).where(DuelEntry.user_id == user.id, DuelEntry.submitted_at.is_not(None))
        )
    ).one()
    wins = (
        await session.execute(
            select(func.count(DuelEntry.id)).where(
                DuelEntry.user_id == user.id, DuelEntry.is_winner.is_(True)
            )
        )
    ).scalar_one()

    insp = (
        await session.execute(
            select(
                func.count(InspectorRound.id),
                func.coalesce(
                    func.sum(func.cast(InspectorRound.is_correct, Integer)),
                    0,
                ),
            ).where(InspectorRound.user_id == user.id)
        )
    ).one()

    duel_points = int(duel_rows[1] or 0)
    insp_total, insp_correct = int(insp[0] or 0), int(insp[1] or 0)
    insp_points = (
        insp_correct * games.POINTS_INSPECTOR_CORRECT
        + (insp_total - insp_correct) * games.POINTS_INSPECTOR_WRONG
    )

    return GameStatsOut(
        duels_played=int(duel_rows[0] or 0),
        duels_won=wins,
        duel_points=duel_points,
        inspector_rounds=insp_total,
        inspector_correct=insp_correct,
        inspector_accuracy=games.inspector_accuracy(insp_correct, insp_total),
        total_points=duel_points + insp_points,
    )


@router.get("/leaderboard", response_model=LeaderboardOut, summary="Рейтинг игроков")
async def leaderboard(
    user: ObserverUser, session: SessionDep, limit: int = 20
) -> LeaderboardOut:
    rows = (
        await session.execute(
            select(
                DuelEntry.user_id,
                func.coalesce(func.sum(DuelEntry.score), 0).label("points"),
                func.sum(func.cast(DuelEntry.is_winner, Integer)).label("wins"),
            )
            .group_by(DuelEntry.user_id)
            .order_by(func.coalesce(func.sum(DuelEntry.score), 0).desc())
        )
    ).all()

    user_ids = [r[0] for r in rows]
    players = {}
    if user_ids:
        found = (
            await session.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
        players = {u.id: u for u in found}

    entries: list[LeaderboardEntry] = []
    my_rank, my_points = None, 0
    for i, (uid, points, wins) in enumerate(rows, start=1):
        player = players.get(uid)
        if player is None:
            continue
        if uid == user.id:
            my_rank, my_points = i, int(points or 0)
        if len(entries) < limit:
            entries.append(
                LeaderboardEntry(
                    rank=i,
                    display_name=_display_name(player),
                    points=int(points or 0),
                    duels_won=int(wins or 0),
                )
            )

    return LeaderboardOut(entries=entries, my_rank=my_rank, my_points=my_points)
