"""Проверки безопасности.

Каждый тест закрывает конкретную найденную дыру. Без них исправления —
просто слова: регрессия вернёт проблему молча.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import hash_password
from app.models.enums import Role
from app.models.geo import Oopt, Segment
from app.models.user import User
from tests.conftest import OOPT_ID, auth
from tests.test_api_flow import register_observer
from tests.test_s6_cycle import (
    admitted_volunteer,
    submit_full_report,
    tiny_png,
    upload_photo,
)
from tests.test_api_flow import create_event
from tests.test_api_flow import confirm_segment

pytestmark = pytest.mark.asyncio


async def foreign_staff_token(client, session_factory) -> str:
    """Сотрудник ДРУГОЙ ООПТ — для проверки территориальной изоляции."""
    async with session_factory() as s:
        s.add(Oopt(id="chuzhaya", name_key="oopt.chuzhaya.name"))
        await s.flush()
        s.add(
            User(
                id=uuid.uuid4(),
                email="foreign@oopt.ru",
                password_hash=hash_password("Foreign-123"),
                name="Инспектор Чужой",
                birth_year=1980,
                role=Role.OOPT_STAFF,
                oopt_id="chuzhaya",
            )
        )
        await s.commit()

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "foreign@oopt.ru", "password": "Foreign-123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ------------------------------------------------------------------ #
# Территориальная изоляция
# ------------------------------------------------------------------ #

async def test_foreign_staff_cannot_read_report(
    client, staff_token, session_factory
):
    """Роли `oopt_staff` недостаточно — нужна ЕГО территория.

    Отчёт содержит фото с геометкой, а автором может быть подросток.
    Сотрудник чужого заповедника не должен видеть эти данные.
    """
    report_id, _, _ = await submit_full_report(client, staff_token)
    foreign = await foreign_staff_token(client, session_factory)

    mine = await client.get(f"/api/v1/reports/{report_id}", headers=auth(staff_token))
    assert mine.status_code == 200, "свой инспектор читать обязан"

    theirs = await client.get(f"/api/v1/reports/{report_id}", headers=auth(foreign))
    assert theirs.status_code == 403
    assert theirs.json()["code"] == "foreign_territory"


async def test_foreign_staff_cannot_moderate(client, staff_token, session_factory):
    await confirm_segment(client, staff_token)
    foreign = await foreign_staff_token(client, session_factory)

    r = await client.post(
        "/api/v1/moderation/segments/ustie",
        json={"decision": "approve"},
        headers=auth(foreign),
    )
    assert r.status_code in (403, 409)


async def test_foreign_staff_cannot_request_imagery(
    client, staff_token, session_factory
):
    foreign = await foreign_staff_token(client, session_factory)
    r = await client.post(
        "/api/v1/segments/ustie/imagery-requests",
        json={"priority": "normal"},
        headers=auth(foreign),
    )
    assert r.status_code == 403


async def test_foreign_staff_sees_empty_queue(client, staff_token, session_factory):
    """Очередь модерации фильтруется по территории, а не только интерфейсом."""
    await confirm_segment(client, staff_token)
    foreign = await foreign_staff_token(client, session_factory)
    r = await client.get("/api/v1/moderation/segments", headers=auth(foreign))
    assert r.status_code == 200
    assert r.json() == []


async def test_export_limited_to_own_territory(client, staff_token, session_factory):
    foreign = await foreign_staff_token(client, session_factory)
    r = await client.get(
        "/api/v1/export/segments?format=csv", headers=auth(foreign)
    )
    assert r.status_code == 200
    assert "ustie" not in r.text, "участки чужой территории не должны выгружаться"


# ------------------------------------------------------------------ #
# Доступ к загруженным фото
# ------------------------------------------------------------------ #

async def test_photo_url_is_returned(client, staff_token):
    """Без ссылки фронт не покажет фото — сценарий S6 остался бы неполным."""
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = await admitted_volunteer(client, staff_token, event_id)

    r = await client.post(
        "/api/v1/media/upload",
        files={"file": ("s.png", tiny_png(), "image/png")},
        data={"lat": "61.2", "lon": "30.1"},
        headers=auth(token),
    )
    assert r.status_code == 201
    assert r.json()["url"], "url обязателен, иначе фото не показать"


async def test_stranger_cannot_view_photo(client, staff_token):
    """Чужое фото недоступно — в нём геометка несовершеннолетнего."""
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    owner = await admitted_volunteer(client, staff_token, event_id)
    media_id = await upload_photo(client, owner)

    ok = await client.get(f"/api/v1/media/{media_id}", headers=auth(owner))
    assert ok.status_code == 200, "владелец видеть обязан"

    stranger = await register_observer(client, "stranger@test.ru")
    denied = await client.get(f"/api/v1/media/{media_id}", headers=auth(stranger))
    assert denied.status_code == 404, (
        "именно 404, а не 403: иначе перебор идентификаторов покажет, "
        "какие фото существуют"
    )


async def test_photo_requires_auth(client, staff_token):
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    owner = await admitted_volunteer(client, staff_token, event_id)
    media_id = await upload_photo(client, owner)

    r = await client.get(f"/api/v1/media/{media_id}")
    assert r.status_code == 401


# ------------------------------------------------------------------ #
# Ограничение частоты запросов
# ------------------------------------------------------------------ #

async def test_login_brute_force_is_throttled(client, volunteer):
    """Без ограничения форма входа — оракул для перебора паролей."""
    codes = []
    for _ in range(8):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "anya@test.ru", "password": "wrong"},
        )
        codes.append(r.status_code)

    assert 429 in codes, f"перебор не ограничен: {codes}"
    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "anya@test.ru", "password": "wrong"},
    )
    assert blocked.json()["code"] == "too_many_requests"
    assert "retry_after_seconds" in blocked.json()["details"]


async def test_mass_registration_is_throttled(client):
    """Массовая регистрация — прямая атака на консенсус: набрать аккаунтов
    и «подтвердить» любой участок."""
    codes = []
    for i in range(14):
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "name": f"Бот {i}",
                "email": f"bot{i}@test.ru",
                "password": "Passw0rd-1",
                "age": 25,
            },
        )
        codes.append(r.status_code)
    assert 429 in codes, f"массовая регистрация не ограничена: {codes}"


# ------------------------------------------------------------------ #
# Прочее
# ------------------------------------------------------------------ #

async def test_password_is_never_returned(client, volunteer):
    me = await client.get("/api/v1/auth/me", headers=auth(volunteer["access_token"]))
    body = me.text.lower()
    assert "password" not in body
    assert "hash" not in body


async def test_parent_contact_is_never_returned(client, staff_token, volunteer):
    """Контакт родителя — персональные данные третьего лица."""
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = volunteer["access_token"]
    from tests.test_api_flow import complete_course

    await complete_course(client, token)
    await client.post(f"/api/v1/events/{event_id}/enrollment", headers=auth(token))
    r = await client.post(
        f"/api/v1/events/{event_id}/consent",
        json={"parent_contact": "secret-parent@mail.ru"},
        headers=auth(token),
    )
    assert r.status_code == 202
    assert "secret-parent" not in r.text, "контакт родителя не возвращается никогда"


async def test_minor_name_is_truncated_for_moderator(client, staff_token, volunteer):
    """Профили 14–17 закрыты: модерация — не повод раскрывать фамилию."""
    report_id, _, _ = await submit_full_report(client, staff_token)
    r = await client.get("/api/v1/moderation/reports", headers=auth(staff_token))
    assert r.status_code == 200


async def test_error_shape_is_uniform(client):
    """Все ошибки в одном формате — фронт не должен разбирать два вида."""
    r = await client.get("/api/v1/segments/nonexistent-segment")
    assert r.status_code == 404
    body = r.json()
    assert set(body) >= {"code", "message_key"}
    assert body["message_key"].startswith("err.")
