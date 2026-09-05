"""Акции: создание, запись, допуск.

Полевой контур системы. Здесь волонтёр переходит из онлайна в поле —
самый узкий переход воронки (риск R-P2) и самый ответственный с точки
зрения безопасности.

Онлайн-ветка участия при этом остаётся полноценной: разметка не требует
выезда, и волонтёр, который никогда не поедет в поле, всё равно приносит
пользу. Поле — не обязанность, а следующий уровень участия.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core import errors
from app.core.deps import CurrentUser, ObserverUser, SessionDep, StaffUser
from app.core.ratelimit import client_key, consent_limiter
from app.db.base import as_utc, utcnow
from app.models.annotation import Annotation
from app.models.enums import (
    AuditAction,
    ConsentStatus,
    EnrollmentStatus,
    EventStatus,
    Role,
    SegmentStatus,
)
from app.models.event import Consent, Enrollment, Event
from app.models.geo import Segment
from app.schemas.event import ConsentRequest, EnrollmentOut, EventCreate, EventOut
from app.services import audit
from app.services import enrollment as enroll_svc
from app.services.notifications import notifications

router = APIRouter(prefix="/events", tags=["events"])


async def _to_out(session, event: Event, user) -> EventOut:
    enrolled = (
        await session.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.event_id == event.id,
                Enrollment.status != EnrollmentStatus.CANCELLED,
            )
        )
    ).scalar_one()

    # Размечал ли пользователь этот участок — на этом держится механика
    # персональной привязки: «ты нашёл этот участок, ты его и убираешь».
    is_mine = False
    if user is not None:
        is_mine = (
            await session.execute(
                select(func.count())
                .select_from(Annotation)
                .where(
                    Annotation.user_id == user.id,
                    Annotation.segment_id == event.segment_id,
                )
            )
        ).scalar_one() > 0

    return EventOut(
        id=event.id,
        segment_id=event.segment_id,
        segment_name_key=event.segment.name_key if event.segment else "",
        oopt_id=event.oopt_id,
        starts_at=event.starts_at,
        capacity=event.capacity,
        enrolled_count=enrolled,
        meeting_point=event.meeting_point,
        status=event.status,
        is_my_segment=is_mine,
    )


@router.get("", response_model=list[EventOut], summary="Список акций")
async def list_events(
    user: CurrentUser,
    session: SessionDep,
    segment_id: str | None = Query(default=None),
    upcoming_only: bool = Query(default=True),
) -> list[EventOut]:
    query = (
        select(Event)
        .options(selectinload(Event.segment))
        .order_by(Event.starts_at)
    )
    if segment_id:
        query = query.where(Event.segment_id == segment_id)
    if upcoming_only:
        query = query.where(
            Event.starts_at >= utcnow(), Event.status == EventStatus.PLANNED
        )

    events = (await session.execute(query)).scalars().all()
    return [await _to_out(session, e, user) for e in events]


@router.post(
    "",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать акцию",
)
async def create_event(
    payload: EventCreate, staff: StaffUser, session: SessionDep
) -> EventOut:
    """Создать акцию может только сотрудник ООПТ.

    Волонтёр не назначает выход на охраняемую территорию — это требование
    режима ООПТ, а не продуктовое ограничение.
    """
    segment = await session.get(Segment, payload.segment_id)
    if segment is None:
        raise errors.not_found("segment_not_found")
    if staff.role != Role.ADMIN and segment.oopt_id != staff.oopt_id:
        raise errors.forbidden("foreign_territory")

    # Акция имеет смысл только на подтверждённом участке: иначе волонтёров
    # везут туда, где проблема ещё не установлена.
    if not segment.verified or segment.status != SegmentStatus.WORK:
        raise errors.unprocessable(
            "segment_not_confirmed", status=segment.status.value
        )
    # Время от клиента может прийти без зоны — приводим к UTC перед сравнением.
    if as_utc(payload.starts_at) <= utcnow():
        raise errors.unprocessable("starts_at_in_past")

    event = Event(
        segment_id=segment.id,
        oopt_id=segment.oopt_id,
        created_by_id=staff.id,
        starts_at=payload.starts_at,
        capacity=payload.capacity,
        meeting_point=payload.meeting_point,
    )
    session.add(event)
    await session.flush()

    audit.record(
        session,
        action=AuditAction.EVENT_CREATED,
        entity_type="event",
        entity_id=str(event.id),
        actor_id=staff.id,
        payload={"segment_id": segment.id, "capacity": payload.capacity},
    )

    await session.commit()
    await session.refresh(event)
    event.segment = segment
    return await _to_out(session, event, staff)


@router.post(
    "/{event_id}/enrollment",
    response_model=EnrollmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Записаться на акцию",
)
async def enroll(
    event_id: uuid.UUID, user: ObserverUser, session: SessionDep
) -> EnrollmentOut:
    """Записаться и получить список невыполненных условий допуска.

    Фронт ведёт пользователя по `blocking_requirements`: согласие родителя,
    затем инструктаж. Решать, что можно пропустить, интерфейс не может —
    список формирует сервер.
    """
    # Блокируем строку акции на время проверки мест: без этого пять
    # одновременных запросов при одном свободном месте дают пять записей,
    # а вместимость выезда — это места в транспорте и норма сопровождающих.
    # На SQLite блокировка игнорируется, но там запись и так сериализуется.
    event = (
        await session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        )
    ).scalar_one_or_none()
    if event is None:
        raise errors.not_found("event_not_found")
    if event.status != EventStatus.PLANNED:
        raise errors.conflict("event_not_open", status=event.status.value)
    if as_utc(event.starts_at) <= utcnow():
        # Статуса PLANNED недостаточно: акция могла просто пройти, а статус
        # никто не перевёл. Запись на вчерашний выезд бессмысленна.
        raise errors.conflict("event_already_started")

    taken = (
        await session.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.event_id == event_id,
                Enrollment.status != EnrollmentStatus.CANCELLED,
            )
        )
    ).scalar_one()
    if taken >= event.capacity:
        raise errors.conflict("event_full")

    enrollment = Enrollment(
        user_id=user.id,
        event_id=event_id,
        consent_status=(
            ConsentStatus.NOT_REQUIRED if not user.is_minor else ConsentStatus.REQUESTED
        ),
    )
    session.add(enrollment)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise errors.conflict("already_enrolled") from exc

    # Запись только что создана, согласия у неё заведомо нет. Помечаем связь
    # как загруженную явно: иначе проверка допуска дёрнет ленивую подгрузку
    # в синхронном контексте и упадёт с MissingGreenlet.
    set_committed_value(enrollment, "consent", None)

    enroll_svc.sync_status(user, enrollment)
    await session.commit()
    await session.refresh(enrollment)

    return EnrollmentOut(
        event_id=event_id,
        status=enrollment.status,
        blocking_requirements=enroll_svc.evaluate_requirements(user, enrollment),
        consent_status=enrollment.consent_status,
        briefing_completed_at=enrollment.briefing_completed_at,
    )


@router.post(
    "/{event_id}/consent",
    response_model=EnrollmentOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запросить согласие законного представителя",
)
async def request_consent(
    event_id: uuid.UUID,
    payload: ConsentRequest,
    user: CurrentUser,
    session: SessionDep,
) -> EnrollmentOut:
    """Отправить родителю ссылку на подписание согласия.

    Отправка запроса согласием не является: допуск открывается только
    после фактического подписания. Проверку выполняет сервер.
    """
    if not user.is_minor:
        raise errors.unprocessable("consent_not_required")

    enrollment = (
        await session.execute(
            select(Enrollment)
            .where(Enrollment.user_id == user.id, Enrollment.event_id == event_id)
            .options(selectinload(Enrollment.consent))
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise errors.not_found("enrollment_not_found")

    if enrollment.consent is not None and enrollment.consent.is_signed:
        raise errors.conflict("consent_already_signed")

    token = secrets.token_urlsafe(32)
    if enrollment.consent is None:
        enrollment.consent = Consent(
            parent_contact=payload.parent_contact,
            token=token,
            requested_at=utcnow(),
        )
    else:
        enrollment.consent.parent_contact = payload.parent_contact
        enrollment.consent.token = token
        enrollment.consent.requested_at = utcnow()

    enrollment.consent_status = ConsentStatus.REQUESTED
    await session.commit()
    await session.refresh(enrollment)

    await notifications.request_parent_consent(
        payload.parent_contact,
        child_name=user.name,
        sign_url=f"/consent/{token}",
    )

    return EnrollmentOut(
        event_id=event_id,
        status=enrollment.status,
        blocking_requirements=enroll_svc.evaluate_requirements(user, enrollment),
        consent_status=enrollment.consent_status,
        briefing_completed_at=enrollment.briefing_completed_at,
    )


@router.post(
    "/{event_id}/consent/{token}/sign",
    response_model=EnrollmentOut,
    summary="Подписание согласия родителем",
)
async def sign_consent(
    request: Request, event_id: uuid.UUID, token: str, session: SessionDep
) -> EnrollmentOut:
    """Подтверждение по одноразовой ссылке.

    Без авторизации: у родителя нет аккаунта в системе, и заводить его
    ради одного действия — лишний сбор персональных данных. Защита —
    неугадываемый токен с ограниченным сроком.
    """
    # Токен неугадываем, но перебор всё равно ограничиваем: эндпоинт
    # публичный, и без лимита он остаётся бесплатным полигоном.
    consent_limiter.check(client_key(request))

    consent = (
        await session.execute(select(Consent).where(Consent.token == token))
    ).scalar_one_or_none()

    if consent is None:
        raise errors.not_found("consent_not_found")
    if consent.is_signed:
        raise errors.conflict("consent_already_signed")
    if utcnow() - as_utc(consent.requested_at) > timedelta(days=14):
        raise errors.unprocessable("consent_link_expired")

    enrollment = (
        await session.execute(
            select(Enrollment)
            .where(Enrollment.id == consent.enrollment_id)
            .options(selectinload(Enrollment.consent), selectinload(Enrollment.user))
        )
    ).scalar_one()

    if enrollment.event_id != event_id:
        raise errors.not_found("consent_not_found")

    consent.signed_at = utcnow()
    enrollment.consent_status = ConsentStatus.SIGNED
    enroll_svc.sync_status(enrollment.user, enrollment)

    audit.record(
        session,
        action=AuditAction.CONSENT_SIGNED,
        entity_type="enrollment",
        entity_id=str(enrollment.id),
        actor_id=None,
        payload={"event_id": str(event_id)},
    )

    await session.commit()
    await session.refresh(enrollment)

    return EnrollmentOut(
        event_id=event_id,
        status=enrollment.status,
        blocking_requirements=enroll_svc.evaluate_requirements(
            enrollment.user, enrollment
        ),
        consent_status=enrollment.consent_status,
        briefing_completed_at=enrollment.briefing_completed_at,
    )


@router.post(
    "/{event_id}/briefing",
    response_model=EnrollmentOut,
    summary="Подтвердить прохождение инструктажа",
)
async def complete_briefing(
    event_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> EnrollmentOut:
    enrollment = (
        await session.execute(
            select(Enrollment)
            .where(Enrollment.user_id == user.id, Enrollment.event_id == event_id)
            .options(selectinload(Enrollment.consent))
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise errors.not_found("enrollment_not_found")

    if enrollment.briefing_completed_at is None:
        enrollment.briefing_completed_at = utcnow()

    enroll_svc.sync_status(user, enrollment)
    await session.commit()
    await session.refresh(enrollment)

    return EnrollmentOut(
        event_id=event_id,
        status=enrollment.status,
        blocking_requirements=enroll_svc.evaluate_requirements(user, enrollment),
        consent_status=enrollment.consent_status,
        briefing_completed_at=enrollment.briefing_completed_at,
    )
