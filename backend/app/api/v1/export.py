"""Выгрузка данных для отчётности ООПТ.

Барьер второй аудитории — «новый инструмент должен встраиваться в
существующую отчётность, а не добавлять ещё одну». Поэтому выгрузка отдаёт
готовый файл в форматах, которые сотрудник уже использует: GeoJSON для ГИС
и CSV для таблиц и служебных записок.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from app.core.deps import SessionDep, StaffUser
from app.models.enums import Role
from app.models.geo import Segment

router = APIRouter(prefix="/export", tags=["export"])

COLUMNS = [
    "id",
    "name_key",
    "oopt_id",
    "status",
    "attention_index",
    "factor_s",
    "factor_c",
    "factor_t",
    "factor_a",
    "growth_rate",
    "is_growing",
    "votes",
    "verified",
    "length_km",
    "last_verified_at",
]


@router.get("/segments", summary="Выгрузка участков для отчётности")
async def export_segments(
    staff: StaffUser,
    session: SessionDep,
    format: Annotated[Literal["geojson", "csv"], Query()],
) -> Response:
    """Выгрузить участки территории в GeoJSON или CSV.

    Сотрудник получает только свою территорию — то же ограничение, что и
    во всех остальных запросах, и проверяется оно на сервере.
    """
    query = select(Segment).order_by(Segment.attention_index.desc())
    if staff.role != Role.ADMIN:
        query = query.where(Segment.oopt_id == staff.oopt_id)

    segments = (await session.execute(query)).scalars().all()

    if format == "geojson":
        features = []
        for s in segments:
            geometry = s.geometry or {}
            # Служебные поля демо-данных в выгрузку не попадают.
            geometry = {
                k: v for k, v in geometry.items() if k in ("type", "coordinates")
            } or None
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {c: _value(s, c) for c in COLUMNS},
                }
            )
        body = json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            content=body,
            media_type="application/geo+json",
            headers={
                "Content-Disposition": 'attachment; filename="segments.geojson"'
            },
        )

    buffer = io.StringIO()
    # Разделитель «;» и BOM — иначе Excel с русской локалью откроет CSV
    # одной колонкой, и сотрудник решит, что выгрузка сломана.
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(COLUMNS)
    for s in segments:
        writer.writerow([_value(s, c) for c in COLUMNS])

    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="segments.csv"'},
    )


def _value(segment: Segment, column: str):
    value = getattr(segment, column, None)
    if hasattr(value, "value"):      # перечисления
        return value.value
    if hasattr(value, "isoformat"):  # даты
        return value.isoformat()
    return value
