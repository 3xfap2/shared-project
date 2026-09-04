"""Заявки на новую съёмку ДЗЗ.

Делает связь с космическими данными двусторонней: платформа не только
потребляет снимки, но и запрашивает их. Геопортал Роскосмоса принимает
заявки на новую съёмку штатно, поэтому механика опирается на реально
существующую возможность, а не на предположение.

Практический смысл: мониторинг перестаёт зависеть от того, когда спутник
случайно пройдёт над нужным местом. Инспектор запрашивает съёмку тогда,
когда она нужна — например, по растущей аномалии.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.core import errors
from app.core.deps import SessionDep, StaffUser
from app.models.enums import (
    AuditAction,
    ImageryPriority,
    ImageryRequestStatus,
    Role,
)
from app.models.geo import Segment
from app.models.imagery import ImageryRequest
from app.schemas.imagery import ImageryRequestCreate, ImageryRequestOut
from app.services import audit

router = APIRouter(tags=["imagery"])


def _out(request: ImageryRequest) -> ImageryRequestOut:
    return ImageryRequestOut(
        id=request.id,
        segment_id=request.segment_id,
        segment_name_key=request.segment.name_key if request.segment else None,
        oopt_id=request.oopt_id,
        priority=request.priority,
        status=request.status,
        comment=request.comment,
        expected_at=request.expected_at,
        reject_reason=request.reject_reason,
        external_id=request.external_id,
        delivered_at=request.delivered_at,
        created_at=request.created_at,
    )


@router.post(
    "/segments/{segment_id}/imagery-requests",
    response_model=ImageryRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Запросить новую съёмку участка",
)
async def create_request(
    segment_id: str,
    payload: ImageryRequestCreate,
    staff: StaffUser,
    session: SessionDep,
) -> ImageryRequestOut:
    """Отправить оператору ДЗЗ заявку на съёмку участка.

    Повторная заявка по участку с уже активной блокируется: две заявки на
    одну съёмку — лишние деньги и путаница в очереди оператора.
    """
    segment = await session.get(Segment, segment_id)
    if segment is None:
        raise errors.not_found("segment_not_found")
    if staff.role != Role.ADMIN and segment.oopt_id != staff.oopt_id:
        raise errors.forbidden("foreign_territory")

    active = (
        await session.execute(
            select(ImageryRequest).where(
                ImageryRequest.segment_id == segment_id,
                ImageryRequest.status.in_(
                    [ImageryRequestStatus.QUEUED, ImageryRequestStatus.ACCEPTED]
                ),
            )
        )
    ).scalars().first()
    if active is not None:
        raise errors.conflict(
            "imagery_request_already_active", request_id=str(active.id)
        )

    # Растущая аномалия — основание для срочной съёмки: пока идёт обычная
    # очередь оператора, свалка успевает вырасти ещё.
    priority = payload.priority
    if segment.is_growing and priority == ImageryPriority.NORMAL:
        priority = ImageryPriority.URGENT

    request = ImageryRequest(
        segment_id=segment_id,
        oopt_id=segment.oopt_id,
        requested_by_id=staff.id,
        priority=priority,
        comment=payload.comment,
        status=ImageryRequestStatus.QUEUED,
    )
    session.add(request)
    await session.flush()

    audit.record(
        session,
        action=AuditAction.IMAGERY_REQUESTED,
        entity_type="imagery_request",
        entity_id=str(request.id),
        actor_id=staff.id,
        payload={
            "segment_id": segment_id,
            "priority": priority.value,
            "growing": segment.is_growing,
        },
    )

    await session.commit()
    await session.refresh(request)
    return _out(request)


@router.get(
    "/imagery-requests",
    response_model=list[ImageryRequestOut],
    summary="Заявки на съёмку",
)
async def list_requests(
    staff: StaffUser,
    session: SessionDep,
    status_filter: Annotated[
        ImageryRequestStatus | None, Query(alias="status")
    ] = None,
    segment_id: Annotated[str | None, Query()] = None,
) -> list[ImageryRequestOut]:
    """Список заявок территории. Инспектор видит только свою ООПТ."""
    query = select(ImageryRequest).order_by(ImageryRequest.created_at.desc())
    if staff.role != Role.ADMIN:
        query = query.where(ImageryRequest.oopt_id == staff.oopt_id)
    if status_filter:
        query = query.where(ImageryRequest.status == status_filter)
    if segment_id:
        query = query.where(ImageryRequest.segment_id == segment_id)

    requests = (await session.execute(query)).scalars().all()
    return [_out(r) for r in requests]
