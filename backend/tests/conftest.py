"""Общая обвязка тестов.

База — SQLite в файле на время теста. Внешние ключи включаются явно,
иначе SQLite не поймает нарушения целостности, которые поймает PostgreSQL,
и тесты будут зеленее, чем прод.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base, utcnow
from app.db.session import get_session
from app.main import app
from app.models.enums import Role, SceneSource, SegmentStatus
from app.models.geo import Oopt, Scene, Segment
from app.models.user import User
from app.core.security import hash_password
from app.services import attention

OOPT_ID = "sinie-ozera"


@pytest_asyncio.fixture
async def engine(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    eng = create_async_engine(url, future=True)

    @event.listens_for(eng.sync_engine, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def seeded(session_factory):
    """Территория с участками и парой разновременных снимков на каждом."""
    async with session_factory() as s:
        s.add(Oopt(id=OOPT_ID, name_key="oopt.sinie_ozera.name", region="Тестовый край"))
        await s.flush()

        specs = [
            ("ustie", 0.82, 0.55, 0.80, SegmentStatus.PROBLEM, 255),
            ("kosa", 0.61, 0.40, 0.60, SegmentStatus.PROBLEM, 200),
            ("zaliv", 0.34, 0.20, 0.45, SegmentStatus.WATCH, 120),
        ]
        for sid, fs, fc, fa, st, days in specs:
            seg = Segment(
                id=sid,
                oopt_id=OOPT_ID,
                name_key=f"seg.{sid}.name",
                length_km=2.0,
                status=st,
                factor_s=fs,
                factor_c=fc,
                factor_a=fa,
                votes=0,
                created_at=utcnow() - timedelta(days=days + 10),
                last_verified_at=utcnow() - timedelta(days=days),
                geometry={"trainer_truth": {"x": 0.625, "y": 0.369, "radius": 0.12}},
            )
            attention.recalculate(seg)
            s.add(seg)
            await s.flush()

            for i, day in enumerate((date(2024, 6, 1), date(2025, 6, 1))):
                s.add(
                    Scene(
                        segment_id=sid,
                        captured_at=day,
                        source=SceneSource.RESURS_P,
                        resolution_m=1.0,
                        cloud_cover=0.05,
                        tile_url_template=f"https://tiles.test/{sid}/{i}/{{z}}/{{x}}/{{y}}.png",
                    )
                )
        await s.commit()
    return OOPT_ID


@pytest_asyncio.fixture
async def client(session_factory, seeded):
    async def _override():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def staff_token(session_factory, client) -> str:
    """Сотрудник ООПТ создаётся напрямую: эта роль не выдаётся регистрацией,
    её назначает дирекция территории (закрытая институциональная роль)."""
    async with session_factory() as s:
        staff = User(
            id=uuid.uuid4(),
            email="inspector@oopt.ru",
            password_hash=hash_password("Inspector-123"),
            name="Инспектор Тестов",
            birth_year=1985,
            role=Role.OOPT_STAFF,
            oopt_id=OOPT_ID,
        )
        s.add(staff)
        await s.commit()

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "inspector@oopt.ru", "password": "Inspector-123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def volunteer(client):
    """Зарегистрированный несовершеннолетний волонтёр (14–17).

    Берём именно подростка: на нём проверяется самая ответственная логика —
    обязательное согласие законного представителя.
    """
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Аня Тестова",
            "email": "anya@test.ru",
            "password": "Volunteer-123",
            "age": 16,
            "city": "Приозёрск",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def anon_headers() -> dict[str, str]:
    return {}
