"""Участки береговой линии: карта, карточка, снимки, подписки."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.config import settings
from app.core.deps import CurrentUser, OptionalUser, SessionDep, require_roles
from app.models.enums import Role, SegmentStatus, Verdict
from app.models.geo import Oopt, Scene, Segment
from app.models.user import Subscription
from app.schemas.common import Page
from app.schemas.geo import (
    SceneIngest,
    SceneIngestResult,
    SceneOut,
    SegmentDetailOut,
    SegmentOut,
)
from app.services import attention, growth

router = APIRouter(prefix="/segments", tags=["segments"])


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """Разобрать область карты. Кривой bbox — ошибка клиента, а не сервера."""
    parts = raw.split(",")
    if len(parts) != 4:
        raise errors.unprocessable("bbox_malformed", expected="minLon,minLat,maxLon,maxLat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise errors.unprocessable("bbox_not_numeric") from exc

    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise errors.unprocessable("bbox_lon_out_of_range")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise errors.unprocessable("bbox_lat_out_of_range")
    if min_lon > max_lon or min_lat > max_lat:
        raise errors.unprocessable("bbox_inverted")

    return min_lon, min_lat, max_lon, max_lat


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
    region: Annotated[str | None, Query(description="Регион территории")] = None,
    status_filter: Annotated[
        list[SegmentStatus] | None, Query(alias="status")
    ] = None,
    incident: Annotated[
        list[Verdict] | None,
        Query(description="Тип происшествия: dump, litter"),
    ] = None,
    growing: Annotated[
        bool | None, Query(description="Только участки с растущей аномалией")
    ] = None,
    bbox: Annotated[
        str | None,
        Query(
            description=(
                "Видимая область карты: minLon,minLat,maxLon,maxLat. "
                "Отсекает участки за пределами экрана"
            ),
            examples=["29.8,61.0,30.4,61.4"],
        ),
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

    Фильтры складываются: «свалки в Ленинградской области, которые растут,
    в видимой части карты» — один запрос. Тип происшествия отделён от
    статуса намеренно: свалка и общая замусоренность требуют разных бригад
    и разной техники, инспектор ищет их по отдельности.
    """
    query = select(Segment)
    count_query = select(func.count()).select_from(Segment)

    def both(condition):
        nonlocal query, count_query
        query = query.where(condition)
        count_query = count_query.where(condition)

    if oopt_id:
        both(Segment.oopt_id == oopt_id)
    if region:
        # Регион хранится у территории, а не у участка: дублировать его
        # в каждом участке значило бы чинить рассинхрон при переименовании.
        both(Segment.oopt_id.in_(select(Oopt.id).where(Oopt.region == region)))
    if status_filter:
        both(Segment.status.in_(status_filter))
    if incident:
        both(Segment.incident_type.in_(incident))
    if growing is not None:
        threshold = settings.GROWTH_ALERT_THRESHOLD
        both(
            Segment.growth_rate >= threshold
            if growing
            else (
                Segment.growth_rate.is_(None) | (Segment.growth_rate < threshold)
            )
        )
    if bbox:
        min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
        both(Segment.center_lat.between(min_lat, max_lat))
        both(Segment.center_lon.between(min_lon, max_lon))

    order = {
        "attention_desc": Segment.attention_index.desc(),
        "attention_asc": Segment.attention_index.asc(),
        "name": Segment.name_key.asc(),
    }[sort]
    query = query.order_by(order, Segment.id).limit(limit).offset(offset)

    total = (await session.execute(count_query)).scalar_one()
    segments = list((await session.execute(query)).scalars().all())

    subscribed = await _subscribed_ids(session, user)
    is_staff = user is not None and user.is_oopt_staff

    items = []
    for seg in segments:
        item = SegmentOut.model_validate(seg)
        item.is_subscribed = seg.id in subscribed
        if not is_staff:
            # Эталонный пул контрольных заданий — это ровно подтверждённые
            # участки. Публичная выдача `verified` позволяла волонтёру
            # сверить полученное задание со списком и понять, что его
            # проверяют. Признак нужен только сотруднику ООПТ.
            item.verified = False
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


@router.post(
    "/{segment_id}/scenes",
    response_model=SceneIngestResult,
    status_code=status.HTTP_201_CREATED,
    summary="Принять обработанную сцену от конвейера ДЗЗ",
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
async def ingest_scene(
    segment_id: str, payload: SceneIngest, session: SessionDep
) -> SceneIngestResult:
    """Стыковка E4 → C6: конвейер ДЗЗ передаёт результат обработки растра.

    Бэкенд не работает с пикселями. Он принимает метаданные сцены, площадь
    выявленной аномалии и готовый сигнал S, после чего сам определяет
    динамику: если площадь выросла между двумя последними измерениями,
    сигнал усиливается, а участок получает метку «растёт».

    Именно здесь детектор роста включается в работу — до появления этого
    эндпоинта он существовал только в тестах, и заявленная в паспорте
    фича на живой системе не проявлялась никак.
    """
    segment = (
        await session.execute(
            select(Segment)
            .where(Segment.id == segment_id)
            .options(selectinload(Segment.scenes))
        )
    ).scalar_one_or_none()
    if segment is None:
        raise errors.not_found("segment_not_found")

    index_before = segment.attention_index

    scene = Scene(
        segment_id=segment.id,
        captured_at=payload.captured_at,
        source=payload.source,
        resolution_m=payload.resolution_m,
        cloud_cover=payload.cloud_cover,
        tile_url_template=payload.tile_url_template,
        anomaly_area_m2=payload.anomaly_area_m2,
    )
    session.add(scene)
    await session.flush()

    if payload.signal is not None:
        segment.factor_s = payload.signal

    # Динамика считается по всему ряду измеренных сцен, включая новую.
    result = growth.apply_growth(segment, list(segment.scenes) + [scene])
    attention.recalculate(segment)

    await session.commit()
    await session.refresh(segment)
    await session.refresh(scene)

    return SceneIngestResult(
        scene=SceneOut.model_validate(scene),
        segment=SegmentOut.model_validate(segment),
        growth_rate=result.rate,
        is_growing=segment.is_growing,
        attention_index_before=index_before,
        attention_index_after=segment.attention_index,
    )
