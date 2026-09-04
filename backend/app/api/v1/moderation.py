"""Рабочее место сотрудника ООПТ: очереди модерации и решения.

ПРИНЦИП, ЗАЛОЖЕННЫЙ В ЭТОТ МОДУЛЬ. Сотрудник ООПТ никогда не делает
работу, которую может сделать волонтёр или система. Он только принимает
решение по готовому: подтвердить или отклонить. Всё остальное — сбор,
агрегация, пересчёт — уже выполнено к моменту, когда он открыл экран.

Территориальное ограничение проверяется на сервере, а не в интерфейсе:
сотрудник видит и меняет только свою ООПТ.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.deps import SessionDep, StaffUser
from app.db.base import utcnow
from app.models.annotation import Annotation
from app.models.enums import (
    AuditAction,
    Verdict,
    ModerationDecision,
    ReportStatus,
    Role,
    SegmentStatus,
)
from app.models.geo import Segment
from app.models.report import FieldReport
from app.models.user import Subscription, User
from app.schemas.annotation import (
    BatchModerationIn,
    BatchModerationResult,
    DigestOut,
    Mark,
    ModerationDecisionIn,
    ModerationItemOut,
)
from app.schemas.geo import SegmentOut
from app.schemas.report import FieldReportOut, PhotoRefOut, ReportDecisionOut
from app.services import attention, audit, calibration, consensus
from app.services.notifications import notifications

router = APIRouter(prefix="/moderation", tags=["moderation"])

VOLUNTEER_HOURS_PER_REPORT = 4.0


def _prevailing_verdict(segment) -> Verdict:
    """Преобладающий вердикт разметчиков — эталон при подтверждении.

    Контрольные разметки исключаем: они не участвовали в решении.
    """
    counts: dict[Verdict, float] = {}
    for a in segment.annotations:
        if a.is_control:
            continue
        counts[a.verdict] = counts.get(a.verdict, 0.0) + max(a.weight, 0.0)
    if not counts:
        return Verdict.NONE
    return max(counts, key=counts.__getitem__)


def _check_territory(staff: User, oopt_id: str) -> None:
    """Сотрудник работает только со своей территорией. Администратор — со всеми."""
    if staff.role != Role.ADMIN and staff.oopt_id != oopt_id:
        raise errors.forbidden("foreign_territory")


# ------------------------------------------------------------------ #
# Очередь разметки
# ------------------------------------------------------------------ #

@router.get(
    "/segments",
    response_model=list[ModerationItemOut],
    summary="Очередь модерации разметки",
)
async def moderation_queue(
    staff: StaffUser, session: SessionDep
) -> list[ModerationItemOut]:
    query = (
        select(Segment)
        .where(Segment.queued_at.is_not(None), Segment.verified.is_(False))
        .options(selectinload(Segment.annotations))
        .order_by(Segment.attention_index.desc())
    )
    if staff.role != Role.ADMIN:
        query = query.where(Segment.oopt_id == staff.oopt_id)

    segments = (await session.execute(query)).scalars().all()

    items: list[ModerationItemOut] = []
    for segment in segments:
        result = consensus.evaluate(segment.annotations)

        # Отметки всех разметчиков сводятся в одну картину: инспектору
        # нужно увидеть, куда смотреть, а не читать список координат.
        heatmap: list[Mark] = []
        for ann in segment.annotations:
            for mark in ann.marks or []:
                heatmap.append(Mark(**mark))

        # Точность разметчиков — то, ради чего делались контрольные задания.
        author_ids = [a.user_id for a in segment.annotations if a.user_id]
        accuracy = None
        if author_ids:
            authors = (
                await session.execute(select(User).where(User.id.in_(author_ids)))
            ).scalars().all()
            measured = [u.accuracy for u in authors if u.accuracy is not None]
            if measured:
                accuracy = sum(measured) / len(measured)

        items.append(
            ModerationItemOut(
                segment=SegmentOut.model_validate(segment),
                annotations_count=result.total,
                agreement=result.agreement,
                marks_heatmap=heatmap[:100],
                queued_at=segment.queued_at,
                annotators_accuracy=accuracy,
            )
        )
    return items


@router.post(
    "/segments/batch",
    response_model=BatchModerationResult,
    summary="Пакетное решение по нескольким участкам",
)
async def decide_segments_batch(
    payload: BatchModerationIn, staff: StaffUser, session: SessionDep
) -> BatchModerationResult:
    """Подтвердить или отклонить сразу несколько однотипных участков.

    Зачем это нужно: соседние участки одного берега после шторма выглядят
    одинаково, и требовать отдельного решения по каждому — верный способ
    добиться того, что инспектор перестанет открывать очередь. Пакетная
    обработка снимает главный барьер второй аудитории — нагрузку.

    Участки, недоступные сотруднику или уже вышедшие из очереди,
    пропускаются с указанием причины, а не роняют весь запрос: инспектор
    должен увидеть, что именно не применилось.
    """
    segments = (
        await session.execute(
            select(Segment)
            .where(Segment.id.in_(payload.segment_ids))
            .options(selectinload(Segment.annotations))
        )
    ).scalars().all()

    found = {s.id: s for s in segments}
    approved = payload.decision == ModerationDecision.APPROVE

    applied: list[str] = []
    skipped: dict[str, str] = {}
    author_ids: set[uuid.UUID] = set()

    for segment_id in payload.segment_ids:
        segment = found.get(segment_id)
        if segment is None:
            skipped[segment_id] = "segment_not_found"
            continue
        if staff.role != Role.ADMIN and segment.oopt_id != staff.oopt_id:
            skipped[segment_id] = "foreign_territory"
            continue
        if segment.queued_at is None:
            skipped[segment_id] = "segment_not_in_queue"
            continue

        previous = segment.status
        if approved:
            segment.verified = True
            segment.status = SegmentStatus.WORK
        else:
            segment.verified = False
            segment.queued_at = None
            segment.votes = 0
            segment.status = SegmentStatus.WATCH
            segment.factor_c = max(0.0, segment.factor_c - 0.25)

        author_ids.update(a.user_id for a in segment.annotations if a.user_id)
        attention.recalculate(segment)

        audit.record(
            session,
            action=(
                AuditAction.SEGMENT_APPROVED
                if approved
                else AuditAction.SEGMENT_REJECTED
            ),
            entity_type="segment",
            entity_id=segment.id,
            actor_id=staff.id,
            payload={
                "status": [previous.value, segment.status.value],
                "comment": payload.comment,
                "batch": True,
            },
        )
        applied.append(segment_id)

    # Репутация правится один раз на пользователя, а не по разу за участок:
    # иначе пакет из десяти участков десятикратно усилил бы эффект.
    if author_ids and applied:
        authors = (
            await session.execute(select(User).where(User.id.in_(author_ids)))
        ).scalars().all()
        for author in authors:
            author.reputation = consensus.adjust_reputation(
                author.reputation, approved=approved
            )

    await session.commit()
    return BatchModerationResult(applied=applied, skipped=skipped)


@router.post(
    "/segments/{segment_id}",
    response_model=SegmentOut,
    summary="Решение по участку",
)
async def decide_segment(
    segment_id: str,
    payload: ModerationDecisionIn,
    staff: StaffUser,
    session: SessionDep,
) -> SegmentOut:
    """Подтвердить или отклонить размеченный участок.

    Решение меняет репутацию всех, кто размечал этот участок: система
    сама вычищает тех, кто отмечает наугад ради баллов.
    """
    segment = (
        await session.execute(
            select(Segment)
            .where(Segment.id == segment_id)
            .options(selectinload(Segment.annotations))
        )
    ).scalar_one_or_none()

    if segment is None:
        raise errors.not_found("segment_not_found")
    _check_territory(staff, segment.oopt_id)
    if segment.queued_at is None:
        raise errors.conflict("segment_not_in_queue")

    approved = payload.decision == ModerationDecision.APPROVE
    previous_status = segment.status

    # Решённый участок пополняет эталонный пул: правильный ответ по нему
    # теперь известен, и его можно незаметно подмешивать в задания для
    # измерения точности разметчиков.
    segment.control_truth = calibration.truth_from_decision(
        approved=approved, prevailing=_prevailing_verdict(segment)
    )
    segment.is_control_pool = True

    if approved:
        segment.verified = True
        segment.status = SegmentStatus.WORK
    else:
        # Участок возвращается в наблюдение: разметка не подтвердилась,
        # но это не значит, что за ним не надо следить.
        segment.verified = False
        segment.queued_at = None
        segment.votes = 0
        segment.status = SegmentStatus.WATCH
        segment.factor_c = max(0.0, segment.factor_c - 0.25)

    # Репутация авторов разметки
    author_ids = {a.user_id for a in segment.annotations if a.user_id}
    if author_ids:
        authors = (
            await session.execute(select(User).where(User.id.in_(author_ids)))
        ).scalars().all()
        for author in authors:
            author.reputation = consensus.adjust_reputation(
                author.reputation, approved=approved
            )

    attention.recalculate(segment)

    audit.record(
        session,
        action=(
            AuditAction.SEGMENT_APPROVED if approved else AuditAction.SEGMENT_REJECTED
        ),
        entity_type="segment",
        entity_id=segment.id,
        actor_id=staff.id,
        payload={
            "status": [previous_status.value, segment.status.value],
            "comment": payload.comment,
            "annotators": len(author_ids),
        },
    )

    await session.commit()
    await session.refresh(segment)
    return SegmentOut.model_validate(segment)


@router.get(
    "/digest",
    response_model=DigestOut,
    summary="Сводка по территории за период",
)
async def territory_digest(
    staff: StaffUser,
    session: SessionDep,
    days: int = 90,
) -> DigestOut:
    """Что дала система территории за период.

    Механика удержания сотрудника ООПТ: без неё система остаётся для него
    источником работы, а не пользы. Сводка отвечает на единственный вопрос,
    который он себе задаёт — «а что я с этого получил».
    """
    period_to = utcnow()
    period_from = period_to - timedelta(days=max(1, min(days, 365)))

    scope = select(Segment)
    if staff.role != Role.ADMIN:
        scope = scope.where(Segment.oopt_id == staff.oopt_id)
    segments = (await session.execute(scope)).scalars().all()
    segment_ids = [s.id for s in segments]

    confirmed = sum(1 for s in segments if s.verified)
    cleaned = sum(1 for s in segments if s.status == SegmentStatus.CLEAN)

    annotations_count = 0
    volunteers = 0
    volume = 0.0

    if segment_ids:
        annotations_count = (
            await session.execute(
                select(func.count())
                .select_from(Annotation)
                .where(
                    Annotation.segment_id.in_(segment_ids),
                    Annotation.created_at >= period_from,
                )
            )
        ).scalar_one()

        volunteers = (
            await session.execute(
                select(func.count(func.distinct(Annotation.user_id))).where(
                    Annotation.segment_id.in_(segment_ids),
                    Annotation.created_at >= period_from,
                )
            )
        ).scalar_one()

        volume = (
            await session.execute(
                select(func.coalesce(func.sum(FieldReport.volume_kg), 0.0)).where(
                    FieldReport.segment_id.in_(segment_ids),
                    FieldReport.status == ReportStatus.APPROVED,
                    FieldReport.moderated_at >= period_from,
                )
            )
        ).scalar_one()

    top = sorted(segments, key=lambda s: s.attention_index, reverse=True)[:5]

    return DigestOut(
        period_from=period_from,
        period_to=period_to,
        segments_total=len(segments),
        segments_confirmed=confirmed,
        segments_cleaned=cleaned,
        annotations_received=annotations_count,
        volunteers_active=volunteers,
        volume_kg_total=float(volume),
        top_segments=[SegmentOut.model_validate(s) for s in top],
    )


# ------------------------------------------------------------------ #
# Очередь полевых отчётов
# ------------------------------------------------------------------ #

@router.get(
    "/reports",
    response_model=list[FieldReportOut],
    summary="Очередь модерации полевых отчётов",
)
async def report_queue(staff: StaffUser, session: SessionDep) -> list[FieldReportOut]:
    query = (
        select(FieldReport)
        .where(FieldReport.status == ReportStatus.PENDING)
        .order_by(FieldReport.submitted_at)
    )
    if staff.role != Role.ADMIN:
        query = query.join(Segment).where(Segment.oopt_id == staff.oopt_id)

    reports = (await session.execute(query)).scalars().all()
    return [_report_out(r) for r in reports]


@router.post(
    "/reports/{report_id}",
    response_model=ReportDecisionOut,
    summary="Решение по отчёту",
)
async def decide_report(
    report_id: uuid.UUID,
    payload: ModerationDecisionIn,
    staff: StaffUser,
    session: SessionDep,
) -> ReportDecisionOut:
    """Подтвердить уборку — момент замыкания цикла.

    Подтверждение переводит участок в статус «чистый», обнуляет фактор
    давности и снижает консенсус: проблема устранена. Индекс внимания
    пересчитывается, участок уходит вниз карты приоритетов, и инспектор
    направляет ресурс на следующий.
    """
    report = await session.get(FieldReport, report_id)
    if report is None:
        raise errors.not_found("report_not_found")
    if report.status != ReportStatus.PENDING:
        raise errors.conflict("report_not_pending", status=report.status.value)

    segment = await session.get(Segment, report.segment_id)
    if segment is None:
        raise errors.not_found("segment_not_found")
    _check_territory(staff, segment.oopt_id)

    approved = payload.decision == ModerationDecision.APPROVE
    index_before = segment.attention_index
    hours = 0.0

    report.moderated_at = utcnow()
    report.moderated_by_id = staff.id
    report.moderator_comment = payload.comment

    if approved:
        report.status = ReportStatus.APPROVED
        segment.status = SegmentStatus.CLEAN
        attention.apply_cleanup(segment)

        author = await session.get(User, report.user_id)
        if author is not None:
            hours = VOLUNTEER_HOURS_PER_REPORT
            author.volunteer_hours += hours
    else:
        report.status = ReportStatus.REJECTED

    audit.record(
        session,
        action=(
            AuditAction.REPORT_APPROVED if approved else AuditAction.REPORT_REJECTED
        ),
        entity_type="field_report",
        entity_id=str(report.id),
        actor_id=staff.id,
        payload={
            "segment_id": segment.id,
            "attention_index": [index_before, segment.attention_index],
            "volume_kg": report.volume_kg,
            "comment": payload.comment,
        },
    )

    await session.commit()
    await session.refresh(report)
    await session.refresh(segment)

    # Уведомления после фиксации транзакции: неотправленное письмо
    # не должно откатывать подтверждённую уборку.
    author = await session.get(User, report.user_id)
    if author is not None:
        await notifications.notify_report_decision(
            author.email, approved=approved, segment_name_key=segment.name_key
        )

    if approved:
        subscriber_emails = (
            await session.execute(
                select(User.email)
                .join(Subscription, Subscription.user_id == User.id)
                .where(Subscription.segment_id == segment.id)
            )
        ).scalars().all()
        if subscriber_emails:
            await notifications.notify_subscribers_new_scene(
                list(subscriber_emails), segment.name_key
            )

    return ReportDecisionOut(
        report=_report_out(report),
        hours_awarded=hours,
        attention_index_before=index_before,
        attention_index_after=segment.attention_index,
    )


def _report_out(report: FieldReport) -> FieldReportOut:
    """Собрать представление отчёта.

    Имя автора-подростка показывается без фамилии: профили 14–17 закрыты,
    и модерация — не повод их раскрывать.
    """
    author = report.author
    display_name = author.name if author else "—"
    if author is not None and author.is_minor:
        display_name = author.name.split(" ")[0]

    return FieldReportOut(
        id=report.id,
        event_id=report.event_id,
        segment_id=report.segment_id,
        segment_name_key=report.segment.name_key if report.segment else None,
        author_name=display_name,
        photo_before=(
            PhotoRefOut.model_validate(report.photo_before)
            if report.photo_before
            else None
        ),
        photo_after=(
            PhotoRefOut.model_validate(report.photo_after)
            if report.photo_after
            else None
        ),
        volume_kg=report.volume_kg,
        comment=report.comment,
        status=report.status,
        submitted_at=report.submitted_at,
        moderated_at=report.moderated_at,
    )
