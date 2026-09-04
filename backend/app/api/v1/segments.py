"""Участки береговой линии: карта, карточка, снимки, подписки."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.deps import CurrentUser, OptionalUser, SessionDep
from app.models.enums import SegmentStatus
from app.models.geo import Segment
from app.models.user import Subscription
from app.schemas.common import Page
from app.schemas.geo import SceneOut, SegmentDetailOut, SegmentOut

router = APIRouter(prefix="/segments", tags=["segments"])


async def _subscribed_ids(session, user) -> set[str]:
    if user is None:
        return set()
    rows = await session.execute(
        select(Subscription.segment_id).where(Subscription.user_id == user.id)
    )
    return set(rows.scalars().all())


@router.get("", response_model=Page[SegmentOut], summary="Участки береговой линии")
async def list_segments(
    session: SessionDep,
    user: OptionalUser,
    oopt_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[
        list[SegmentStatus] | None, Query(alias="status")
    ] = None,
    sort: Annotated[
        Literal["attention_desc", "attention_asc", "name"], Query()
    ] = "attention_desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SegmentOut]:
    """Источник данных для карты и для кабинета ООПТ.

    По умолчанию сортировка по убыванию индекса внимания — это и есть
    карта приоритетов, главный экран инспектора.
    """
    query = select(Segment)
    count_query = select(func.count()).select_from(Segment)

    if oopt_id:
        query = query.where(Segment.oopt_id == oopt_id)
        count_query = count_query.where(Segment.oopt_id == oopt_id)
    if status_filter:
        query = query.where(Segment.status.in_(status_filter))
        count_query = count_query.where(Segment.status.in_(status_filter))

    order = {
        "attention_desc": Segment.attention_index.desc(),
        "attention_asc": Segment.attention_index.asc(),
        "name": Segment.name_key.asc(),
    }[sort]
    query = query.order_by(order, Segment.id).limit(limit).offset(offset)

    total = (await session.execute(count_query)).scalar_one()
    segments = list((await session.execute(query)).scalars().all())

    subscribed = await _subscribed_ids(session, user)
    items = []
    for seg in segments:
        item = SegmentOut.model_validate(seg)
        item.is_subscribed = seg.id in subscribed
        items.append(item)

    return Page[SegmentOut](items=items, total=total)


@router.get(
    "/{segment_id}", response_model=SegmentDetailOut, summary="Карточка участка"
)
async def get_segment(
    segment_id: str, session: SessionDep, user: OptionalUser
) -> SegmentDetailOut:
    segment = (
        await session.execute(
            select(Segment)
            .where(Segment.id == segment_id)
            .options(selectinload(Segment.scenes))
        )
    ).scalar_one_or_none()

    if segment is None:
        raise errors.not_found("segment_not_found")

    detail = SegmentDetailOut.model_validate(segment)
    detail.is_subscribed = segment.id in await _subscribed_ids(session, user)
    detail.scenes = [SceneOut.model_validate(s) for s in segment.scenes]
    return detail


@router.get(
    "/{segment_id}/scenes",
    response_model=list[SceneOut],
    summary="Снимки ДЗЗ по участку",
)
async def list_scenes(segment_id: str, session: SessionDep) -> list[SceneOut]:
    """Разновременные сцены, от новых к старым.

    Дата съёмки обязательна к показу в интерфейсе: пользователь должен
    понимать, что данные обновляются раз в 2–4 недели, а не в реальном
    времени.
    """
    segment = await session.get(Segment, segment_id)
    if segment is None:
        raise errors.not_found("segment_not_found")

    scenes = (
        await session.execute(
            select(Segment)
            .where(Segment.id == segment_id)
            .options(selectinload(Segment.scenes))
        )
    ).scalar_one()

    return [SceneOut.model_validate(s) for s in scenes.scenes]


@router.put(
    "/{segment_id}/subscription",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Подписаться на участок",
)
async def subscribe(
    segment_id: str, user: CurrentUser, session: SessionDep
) -> Response:
    """Механика повторного участия: новый снимок — уведомление подписчику."""
    if await session.get(Segment, segment_id) is None:
        raise errors.not_found("segment_not_found")

    existing = await session.get(Subscription, (user.id, segment_id))
    if existing is None:
        session.add(Subscription(user_id=user.id, segment_id=segment_id))
        await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{segment_id}/subscription",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отписаться от участка",
)
async def unsubscribe(
    segment_id: str, user: CurrentUser, session: SessionDep
) -> Response:
    existing = await session.get(Subscription, (user.id, segment_id))
    if existing is not None:
        await session.delete(existing)
        await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
