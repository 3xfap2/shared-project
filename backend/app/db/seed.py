"""Демонстрационные данные.

Заполняет базу территорией, участками и снимками, чтобы систему можно было
запустить и сразу пройти сценарий S1 → S6 без ручной подготовки.

Значения факторов совпадают с эталонным прототипом: участок «Устье реки
Каменки» после консенсуса даёт индекс 77, после подтверждённой уборки — 41.
Это делает бэкенд и прототип взаимно проверяемыми.

Запуск:  python -m app.db.seed
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base, utcnow
from app.db.session import SessionFactory, engine
from app.models.enums import Role, SceneSource, SegmentStatus
from app.models.geo import Oopt, Scene, Segment
from app.models.user import User
from app.services import attention

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed")

OOPT_ID = "sinie-ozera"

# (id, S, C, A, статус, дней с последней проверки, длина км)
SEGMENTS = [
    ("ustie",   0.82, 0.55, 0.80, SegmentStatus.PROBLEM, 255, 2.4),
    ("kosa",    0.61, 0.40, 0.60, SegmentStatus.PROBLEM, 200, 1.1),
    ("zaliv",   0.34, 0.20, 0.45, SegmentStatus.WATCH,   128, 3.0),
    ("mys",     0.55, 0.15, 0.25, SegmentStatus.WATCH,   292, 0.8),
    ("plyazh",  0.22, 0.10, 0.90, SegmentStatus.CLEAN,    73, 1.6),
    ("starica", 0.48, 0.30, 0.35, SegmentStatus.WATCH,   219, 2.2),
]

# Демо-аккаунты. Пароли заведомо простые — это сиды для локального запуска,
# в проде скрипт не выполняется (проверка ENV ниже).
ACCOUNTS = [
    ("inspector@kosmobereg.ru", "Inspector-123", "Инспектор Сергеев", 1985,
     Role.OOPT_STAFF, OOPT_ID),
    ("admin@kosmobereg.ru", "Admin-123", "Администратор проекта", 1990,
     Role.ADMIN, None),
]


async def seed() -> None:
    from app.core.config import settings

    if settings.ENV in ("staging", "production"):
        raise SystemExit(
            "Сиды не выполняются в окружении "
            f"{settings.ENV}: демо-аккаунты с известными паролями"
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as s:
        if (await s.execute(select(Oopt).limit(1))).scalar_one_or_none():
            log.info("База уже заполнена — пропускаю")
            return

        s.add(
            Oopt(
                id=OOPT_ID,
                name_key="oopt.sinie_ozera.name",
                region="Демонстрационный регион",
                boundary={"type": "Polygon", "coordinates": []},
            )
        )
        await s.flush()

        for sid, fs, fc, fa, status, days, km in SEGMENTS:
            segment = Segment(
                id=sid,
                oopt_id=OOPT_ID,
                name_key=f"seg.{sid}.name",
                length_km=km,
                status=status,
                factor_s=fs,
                factor_c=fc,
                factor_a=fa,
                created_at=utcnow() - timedelta(days=days + 30),
                last_verified_at=utcnow() - timedelta(days=days),
                geometry={
                    "type": "LineString",
                    "coordinates": [],
                    # Эталонная зона для мини-тренажёра сценария S1.
                    "trainer_truth": {"x": 0.625, "y": 0.369, "radius": 0.12},
                },
            )
            attention.recalculate(segment)
            s.add(segment)
            await s.flush()

            for i, captured in enumerate((date(2024, 6, 12), date(2025, 6, 18))):
                s.add(
                    Scene(
                        segment_id=sid,
                        captured_at=captured,
                        source=SceneSource.RESURS_P,
                        resolution_m=1.0,
                        cloud_cover=0.04 + i * 0.02,
                        tile_url_template=(
                            f"https://tiles.kosmobereg.ru/demo/{sid}/{captured:%Y}"
                            "/{z}/{x}/{y}.png"
                        ),
                    )
                )
            log.info("  участок %-9s индекс %s", sid, segment.attention_index)

        for email, password, name, year, role, oopt_id in ACCOUNTS:
            s.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    name=name,
                    birth_year=year,
                    role=role,
                    oopt_id=oopt_id,
                )
            )
            log.info("  аккаунт %-28s роль %s", email, role.value)

        await s.commit()

    log.info("")
    log.info("Готово. Демо-доступы:")
    for email, password, *_ in ACCOUNTS:
        log.info("  %-28s %s", email, password)
    log.info("")
    log.info("Волонтёра заводите через POST /api/v1/auth/register —")
    log.info("так проверяется сценарий S1 целиком.")


if __name__ == "__main__":
    asyncio.run(seed())
