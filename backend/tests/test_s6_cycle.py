"""S6 — полевой отчёт и замыкание цикла.

Главная проверка всей системы: подтверждённая уборка обязана снизить
индекс внимания участка и передать приоритет следующему. Если этого не
происходит, вся модель приоритизации бессмысленна — инспектор будет
раз за разом ездить на уже убранный берег.
"""

from __future__ import annotations

import struct
import zlib
from datetime import timedelta

import pytest

from app.db.base import utcnow
from tests.conftest import auth
from tests.test_api_flow import (
    complete_course,
    confirm_segment,
    create_event,
    register_observer,
)

pytestmark = pytest.mark.asyncio


def tiny_png() -> bytes:
    """Минимальный валидный PNG 1×1 — чтобы не тащить в тесты бинарный файл."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


async def upload_photo(client, token: str, *, lat=61.24, lon=30.11) -> str:
    r = await client.post(
        "/api/v1/media/upload",
        files={"file": ("shot.png", tiny_png(), "image/png")},
        data={"lat": str(lat), "lon": str(lon), "taken_at": utcnow().isoformat()},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["has_geotag"] is True
    return r.json()["id"]


async def admitted_volunteer(client, staff_token, event_id: str) -> str:
    """Совершеннолетний волонтёр с оформленным допуском."""
    token = await register_observer(client, "field@test.ru", age=25)
    r = await client.post(f"/api/v1/events/{event_id}/enrollment", headers=auth(token))
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/v1/events/{event_id}/briefing", headers=auth(token))
    assert r.json()["blocking_requirements"] == [], r.text
    return token


# ------------------------------------------------------------------ #

async def test_media_rejects_wrong_type(client, volunteer):
    r = await client.post(
        "/api/v1/media/upload",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth(volunteer["access_token"]),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "unsupported_media_type"


async def test_report_requires_admission(client, staff_token):
    """Отчёт от недопущенного участника означает, что он был на территории
    без оформленного допуска — принимать такой отчёт нельзя."""
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)

    token = await register_observer(client, "nobrief@test.ru", age=30)
    await client.post(f"/api/v1/events/{event_id}/enrollment", headers=auth(token))
    photo = await upload_photo(client, token)

    r = await client.post(
        "/api/v1/reports",
        json={"event_id": event_id, "photo_before_id": photo},
        headers=auth(token),
    )
    assert r.status_code == 403
    assert r.json()["code"] == "not_admitted"
    assert "briefing" in r.json()["details"]["blocking"]


async def test_report_cannot_be_submitted_without_after_photo(client, staff_token):
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = await admitted_volunteer(client, staff_token, event_id)

    before = await upload_photo(client, token)
    r = await client.post(
        "/api/v1/reports",
        json={"event_id": event_id, "photo_before_id": before},
        headers=auth(token),
    )
    assert r.status_code == 201
    report_id = r.json()["id"]

    r = await client.patch(
        f"/api/v1/reports/{report_id}",
        json={"volume_kg": 100, "submit": True},
        headers=auth(token),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "photo_after_required"


async def submit_full_report(client, staff_token) -> tuple[str, str, int]:
    """Довести участок до отчёта на модерации. Возвращает (report_id, token, индекс до)."""
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = await admitted_volunteer(client, staff_token, event_id)

    before = await upload_photo(client, token)
    r = await client.post(
        "/api/v1/reports",
        json={"event_id": event_id, "photo_before_id": before},
        headers=auth(token),
    )
    report_id = r.json()["id"]

    after = await upload_photo(client, token)
    r = await client.patch(
        f"/api/v1/reports/{report_id}",
        json={"photo_after_id": after, "volume_kg": 145, "submit": True},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"

    index_before = (await client.get("/api/v1/segments/ustie")).json()["attention_index"]
    return report_id, token, index_before


async def test_s6_submitted_report_appears_in_moderation_queue(client, staff_token):
    report_id, _, _ = await submit_full_report(client, staff_token)
    r = await client.get("/api/v1/moderation/reports", headers=auth(staff_token))
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == report_id
    assert r.json()[0]["volume_kg"] == 145


async def test_s6_approval_closes_the_cycle(client, staff_token):
    """ГЛАВНАЯ ПРОВЕРКА: подтверждение уборки замыкает цикл."""
    report_id, token, index_before = await submit_full_report(client, staff_token)

    r = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve", "comment": "Проверено на месте"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["report"]["status"] == "approved"
    assert body["hours_awarded"] == 4
    assert body["attention_index_after"] < body["attention_index_before"], (
        f"индекс обязан снизиться: {body['attention_index_before']} → "
        f"{body['attention_index_after']}"
    )

    segment = (await client.get("/api/v1/segments/ustie")).json()
    assert segment["status"] == "clean"
    assert segment["factors"]["t"] == 0.0, "давность обнуляется — участок только что проверен"
    assert segment["last_verified_at"] is not None

    me = await client.get("/api/v1/auth/me", headers=auth(token))
    assert me.json()["volunteer_hours"] == 4


async def test_s6_priority_moves_to_next_segment(client, staff_token):
    """После уборки инспектор должен ехать уже на другой участок."""
    report_id, _, _ = await submit_full_report(client, staff_token)

    top_before = (await client.get("/api/v1/segments?sort=attention_desc")).json()
    assert top_before["items"][0]["id"] == "ustie"

    await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )

    top_after = (await client.get("/api/v1/segments?sort=attention_desc")).json()
    assert top_after["items"][0]["id"] != "ustie", (
        "убранный участок обязан уступить приоритет следующему"
    )


async def test_s6_rejection_does_not_clean_segment(client, staff_token):
    report_id, token, _ = await submit_full_report(client, staff_token)

    r = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "reject", "comment": "Фото не совпадают с участком"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200
    assert r.json()["report"]["status"] == "rejected"
    assert r.json()["hours_awarded"] == 0

    segment = (await client.get("/api/v1/segments/ustie")).json()
    assert segment["status"] != "clean", "отклонённый отчёт не очищает участок"

    me = await client.get("/api/v1/auth/me", headers=auth(token))
    assert me.json()["volunteer_hours"] == 0


async def test_s6_report_cannot_be_moderated_twice(client, staff_token):
    report_id, _, _ = await submit_full_report(client, staff_token)
    first = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "reject"},
        headers=auth(staff_token),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "report_not_pending"


async def test_s6_subscription_survives_the_cycle(client, staff_token):
    """Подписка на участок — механика повторного участия."""
    report_id, token, _ = await submit_full_report(client, staff_token)

    r = await client.put("/api/v1/segments/ustie/subscription", headers=auth(token))
    assert r.status_code == 204

    await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )

    segment = (await client.get("/api/v1/segments/ustie", headers=auth(token))).json()
    assert segment["is_subscribed"] is True
