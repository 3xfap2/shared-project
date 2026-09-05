"""Полевые отчёты и загрузка фото — сбор ground truth."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core import errors
from app.core.config import settings
from app.core.deps import CurrentUser, ObserverUser, SessionDep
from app.db.base import utcnow
from app.models.enums import EnrollmentStatus, ReportStatus, Role
from app.models.event import Enrollment, Event
from app.models.geo import Segment
from app.models.report import FieldReport, Media
from app.schemas.report import (
    FieldReportOut,
    PhotoRefOut,
    ReportCreate,
    ReportUpdate,
)
from app.services import enrollment as enroll_svc

router = APIRouter(tags=["reports"])

# Расширения по MIME-типу: имя файла от клиента не используется — оно
# управляемо пользователем и может содержать путь.
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


@router.post(
    "/media/upload",
    response_model=PhotoRefOut,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить фото",
)
async def upload_media(
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
    lat: float | None = Form(default=None),
    lon: float | None = Form(default=None),
    taken_at: datetime | None = Form(default=None),
) -> PhotoRefOut:
    """Принять фото и вернуть ссылку на него.

    На MVP файлы кладутся на диск. Интерфейс совпадает с S3: в проде
    меняется только реализация записи, схема ответа остаётся прежней.

    Координаты и время приходят отдельными полями, потому что в офлайн-режиме
    PWA камера может не записать EXIF, а отчёт всё равно нужно принять.
    """
    if file.content_type not in settings.ALLOWED_IMAGE_TYPES:
        raise errors.unprocessable(
            "unsupported_media_type", allowed=settings.ALLOWED_IMAGE_TYPES
        )

    # Читаем с проверкой размера: доверять Content-Length нельзя.
    data = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise errors.APIError(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "file_too_large",
            details={"max_bytes": settings.MAX_UPLOAD_BYTES},
        )
    if not data:
        raise errors.unprocessable("empty_file")

    key = (
        f"reports/{utcnow():%Y/%m}/"
        f"{secrets.token_hex(16)}{EXTENSIONS[file.content_type]}"
    )
    path = Path(settings.MEDIA_ROOT) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    media = Media(
        storage_key=key,
        content_type=file.content_type,
        size_bytes=len(data),
        lat=lat,
        lon=lon,
        taken_at=taken_at,
        has_geotag=lat is not None and lon is not None,
        uploaded_by_id=user.id,
    )
    session.add(media)
    await session.commit()
    await session.refresh(media)

    return _photo(media)


@router.post(
    "/reports",
    response_model=FieldReportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать черновик отчёта (фото «до»)",
)
async def create_report(
    payload: ReportCreate, user: ObserverUser, session: SessionDep
) -> FieldReportOut:
    """Зафиксировать состояние участка до начала уборки.

    Время съёмки сохраняется, но пока не проверяется: EXIF приходит от
    клиента и подделывается тривиально, поэтому строгая проверка без
    доверенного источника времени создавала бы ложное чувство гарантии.
    Реальная защита здесь — модерация: инспектор видит обе фотографии и
    их метки. Ужесточение появится вместе с подписью времени на стороне
    приложения, а не раньше.
    """
    event = await session.get(Event, payload.event_id)
    if event is None:
        raise errors.not_found("event_not_found")

    enrollment = (
        await session.execute(
            select(Enrollment)
            .where(
                Enrollment.user_id == user.id,
                Enrollment.event_id == event.id,
            )
            .options(selectinload(Enrollment.consent))
        )
    ).scalar_one_or_none()

    if enrollment is None:
        raise errors.forbidden("not_enrolled")
    if not enroll_svc.is_admitted(user, enrollment):
        # Отчёт от недопущенного участника означает, что он был на территории
        # без оформленного допуска — принимать такой отчёт нельзя.
        raise errors.forbidden(
            "not_admitted",
            blocking=[
                r.value for r in enroll_svc.evaluate_requirements(user, enrollment)
            ],
        )

    photo = await session.get(Media, payload.photo_before_id)
    if photo is None or photo.uploaded_by_id != user.id:
        raise errors.not_found("media_not_found")

    report = FieldReport(
        user_id=user.id,
        event_id=event.id,
        segment_id=event.segment_id,
        photo_before_id=photo.id,
        status=ReportStatus.DRAFT,
    )
    session.add(report)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Один отчёт на участника и акцию — держит уникальный индекс БД.
        # Проверка в коде не спасала бы от двойной отправки формы.
        raise errors.conflict("report_already_exists") from exc

    await session.refresh(report)
    return _out(report)


@router.patch(
    "/reports/{report_id}",
    response_model=FieldReportOut,
    summary="Дополнить и отправить отчёт",
)
async def update_report(
    report_id: uuid.UUID,
    payload: ReportUpdate,
    user: ObserverUser,
    session: SessionDep,
) -> FieldReportOut:
    report = await session.get(FieldReport, report_id)
    if report is None:
        raise errors.not_found("report_not_found")
    if report.user_id != user.id:
        raise errors.forbidden("not_report_author")
    if report.status not in (ReportStatus.DRAFT, ReportStatus.REJECTED):
        raise errors.conflict("report_not_editable", status=report.status.value)

    if payload.photo_after_id is not None:
        photo = await session.get(Media, payload.photo_after_id)
        if photo is None or photo.uploaded_by_id != user.id:
            raise errors.not_found("media_not_found")
        report.photo_after_id = photo.id

    if payload.volume_kg is not None:
        report.volume_kg = payload.volume_kg
    if payload.comment is not None:
        report.comment = payload.comment

    if payload.submit:
        if report.photo_after_id is None:
            raise errors.unprocessable("photo_after_required")
        report.status = ReportStatus.PENDING
        report.submitted_at = utcnow()

        # Присутствие на акции подтверждается фактом отчёта, а не отметкой
        # организатора: отчёт с геометкой — более сильное доказательство.
        enrollment = (
            await session.execute(
                select(Enrollment).where(
                    Enrollment.user_id == user.id,
                    Enrollment.event_id == report.event_id,
                )
            )
        ).scalar_one_or_none()
        if enrollment is not None and enrollment.attended_at is None:
            enrollment.attended_at = utcnow()
            enrollment.status = EnrollmentStatus.ATTENDED

    await session.commit()
    await session.refresh(report)
    return _out(report)


@router.get(
    "/reports/{report_id}", response_model=FieldReportOut, summary="Карточка отчёта"
)
async def get_report(
    report_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> FieldReportOut:
    """Отчёт доступен автору и сотрудникам ООПТ **своей** территории.

    Проверка территории обязательна, а не желательна: отчёт содержит фото
    с геометкой, а его автором может быть несовершеннолетний. Роли
    `oopt_staff` самой по себе недостаточно — иначе сотрудник любого
    заповедника читал бы данные о подростках со всей страны.
    """
    report = await session.get(FieldReport, report_id)
    if report is None:
        raise errors.not_found("report_not_found")

    if report.user_id == user.id:
        return _out(report)

    if user.role == Role.ADMIN:
        return _out(report)

    if user.role == Role.OOPT_STAFF:
        segment = await session.get(Segment, report.segment_id)
        if segment is not None and segment.oopt_id == user.oopt_id:
            return _out(report)
        raise errors.forbidden("foreign_territory")

    raise errors.forbidden("not_report_author")


@router.get(
    "/media/{media_id}",
    summary="Получить загруженное фото",
    response_class=Response,
)
async def get_media(
    media_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Response:
    """Отдать файл тому, кто имеет на него право.

    Доступ намеренно узкий. Фото полевого отчёта содержит геометку и часто
    снято несовершеннолетним: публичная ссылка означала бы, что координаты
    подростка доступны любому, кто угадает идентификатор.

    Право имеют:
      * автор загрузки;
      * сотрудник ООПТ той территории, к которой относится отчёт с этим фото;
      * администратор проекта.

    Файл отдаётся приложением только на MVP. В проде здесь будет подписанная
    ссылка на объектное хранилище с ограниченным сроком жизни — тогда трафик
    минует API, а срок действия ссылки ограничит утечку.
    """
    media = await session.get(Media, media_id)
    if media is None:
        raise errors.not_found("media_not_found")

    if not await _may_view_media(session, user, media):
        # 404, а не 403: иначе перебор идентификаторов покажет,
        # какие фото вообще существуют.
        raise errors.not_found("media_not_found")

    path = Path(settings.MEDIA_ROOT) / media.storage_key
    if not path.is_file():
        raise errors.not_found("media_file_missing")

    return Response(
        content=path.read_bytes(),
        media_type=media.content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline",
        },
    )


async def _may_view_media(session, user, media: Media) -> bool:
    if media.uploaded_by_id == user.id or user.role == Role.ADMIN:
        return True
    if user.role != Role.OOPT_STAFF:
        return False

    # Сотрудник видит фото, только если оно приложено к отчёту его территории.
    report = (
        await session.execute(
            select(FieldReport).where(
                (FieldReport.photo_before_id == media.id)
                | (FieldReport.photo_after_id == media.id)
            )
        )
    ).scalars().first()
    if report is None:
        return False

    segment = await session.get(Segment, report.segment_id)
    return segment is not None and segment.oopt_id == user.oopt_id


def _photo(media: Media | None) -> PhotoRefOut | None:
    """Собрать ссылку на фото с адресом для загрузки."""
    if media is None:
        return None
    ref = PhotoRefOut.model_validate(media)
    ref.url = f"{settings.API_V1_PREFIX}/media/{media.id}"
    return ref


def _out(report: FieldReport) -> FieldReportOut:
    author = report.author
    display_name = author.name if author else "—"
    if author is not None and author.is_minor:
        # Профили 14–17 закрыты. Раньше показывали первое слово имени, но
        # при обычном для отчётности порядке «Фамилия Имя Отчество» это
        # раскрывало ровно фамилию. Порядок слов угадать нельзя, поэтому
        # отдаём псевдоним: инспектору нужен идентификатор участника,
        # а не его имя.
        display_name = f"Наблюдатель #{str(author.id)[-4:].upper()}"

    return FieldReportOut(
        id=report.id,
        event_id=report.event_id,
        segment_id=report.segment_id,
        segment_name_key=report.segment.name_key if report.segment else None,
        author_name=display_name,
        photo_before=_photo(report.photo_before),
        photo_after=_photo(report.photo_after),
        volume_kg=report.volume_kg,
        comment=report.comment,
        status=report.status,
        submitted_at=report.submitted_at,
        moderated_at=report.moderated_at,
    )
