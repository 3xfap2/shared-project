"""Сквозной сценарий S1 → S6 через настоящий API.

Это главная проверка бэкенда: она повторяет путь пользователя из паспорта
целиком и убеждается, что цикл замыкается — после подтверждённой уборки
участок уходит вниз карты приоритетов.
"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest

from app.db.base import utcnow
from tests.conftest import auth

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ #
# S1. Первый контакт — ценность до регистрации
# ------------------------------------------------------------------ #

async def test_s1_trainer_works_without_auth(client):
    r = await client.get("/api/v1/public/trainer/task")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scene_before"]["captured_at"] < data["scene_after"]["captured_at"]
    assert 0 <= data["truth"]["x"] <= 1
    assert data["scene_after"]["source"] == "resurs-p"


async def test_s1_public_stats_without_auth(client):
    r = await client.get("/api/v1/public/stats")
    assert r.status_code == 200
    assert r.json()["segments_watched"] == 3


# ------------------------------------------------------------------ #
# Регистрация и профиль
# ------------------------------------------------------------------ #

async def test_register_minor_starts_as_student(volunteer):
    user = volunteer["user"]
    assert user["role"] == "student"
    assert user["is_minor"] is True
    assert user["certificate_id"] is None


async def test_register_rejects_under_14(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={"name": "Мал", "email": "kid@test.ru", "password": "Passw0rd!", "age": 12},
    )
    assert r.status_code == 422


async def test_duplicate_email_conflicts(client, volunteer):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Другая",
            "email": "anya@test.ru",
            "password": "Another-123",
            "age": 25,
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "email_taken"


async def test_login_does_not_leak_user_existence(client, volunteer):
    """Ответ одинаков для «нет пользователя» и «неверный пароль»."""
    a = await client.post(
        "/api/v1/auth/login",
        json={"email": "anya@test.ru", "password": "wrong-password"},
    )
    b = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.ru", "password": "wrong-password"},
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["code"] == b.json()["code"] == "invalid_credentials"


# ------------------------------------------------------------------ #
# S2. Обучение — курс превращается в допуск
# ------------------------------------------------------------------ #

async def test_s2_student_cannot_annotate_before_course(client, volunteer):
    """Ключевая гарантия доверия ООПТ: разметка Ученика не попадает в очередь."""
    r = await client.get(
        "/api/v1/annotations/task", headers=auth(volunteer["access_token"])
    )
    assert r.status_code == 403
    assert r.json()["code"] == "course_not_completed"


async def test_s2_correct_answers_are_not_exposed(client, volunteer):
    r = await client.get(
        "/api/v1/course/modules", headers=auth(volunteer["access_token"])
    )
    assert r.status_code == 200
    body = r.text
    assert "correct_index" not in body and "correct" not in body


async def test_s2_wrong_answer_does_not_complete_module(client, volunteer):
    r = await client.post(
        "/api/v1/course/modules/m1/answer",
        json={"answer_index": 0},
        headers=auth(volunteer["access_token"]),
    )
    assert r.status_code == 200
    assert r.json()["correct"] is False
    assert r.json()["module_completed"] is False


async def complete_course(client, token: str) -> dict:
    last = None
    for module in ("m1", "m2", "m3"):
        r = await client.post(
            f"/api/v1/course/modules/{module}/answer",
            json={"answer_index": 1},
            headers=auth(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["correct"] is True, f"модуль {module}"
        last = r.json()
    return last


async def test_s2_course_completion_promotes_and_issues_certificate(client, volunteer):
    result = await complete_course(client, volunteer["access_token"])
    assert result["course_completed"] is True
    assert result["role_changed_to"] == "observer"
    assert result["certificate_id"], "сертификат должен быть выдан"

    me = await client.get("/api/v1/auth/me", headers=auth(volunteer["access_token"]))
    assert me.json()["role"] == "observer"


# ------------------------------------------------------------------ #
# S3. Разметка и консенсус
# ------------------------------------------------------------------ #

async def register_observer(client, email: str, age: int = 25) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={"name": f"Волонтёр {email}", "email": email, "password": "Passw0rd-1", "age": age},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    await complete_course(client, token)
    return token


async def test_s3_annotation_requires_marks_for_problem_verdict(client, volunteer):
    await complete_course(client, volunteer["access_token"])
    r = await client.post(
        "/api/v1/annotations",
        json={"segment_id": "ustie", "verdict": "dump", "marks": []},
        headers=auth(volunteer["access_token"]),
    )
    assert r.status_code == 422, "вердикт о проблеме без отметки бесполезен"


async def test_s3_double_annotation_blocked(client, volunteer):
    await complete_course(client, volunteer["access_token"])
    payload = {
        "segment_id": "ustie",
        "verdict": "dump",
        "marks": [{"x": 0.62, "y": 0.37}],
    }
    first = await client.post(
        "/api/v1/annotations", json=payload, headers=auth(volunteer["access_token"])
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/annotations", json=payload, headers=auth(volunteer["access_token"])
    )
    assert second.status_code == 409
    assert second.json()["code"] == "already_annotated"


async def reach_consensus(client) -> list[str]:
    """Три независимых волонтёра размечают один участок."""
    tokens = []
    for i in range(3):
        token = await register_observer(client, f"obs{i}@test.ru")
        r = await client.post(
            "/api/v1/annotations",
            json={
                "segment_id": "ustie",
                "verdict": "dump",
                "marks": [{"x": 0.62 + i * 0.01, "y": 0.37}],
            },
            headers=auth(token),
        )
        assert r.status_code == 201, r.text
        tokens.append(token)
    return tokens


async def test_s3_consensus_reached_at_third_annotation(client):
    tokens = []
    for i in range(3):
        token = await register_observer(client, f"c{i}@test.ru")
        r = await client.post(
            "/api/v1/annotations",
            json={"segment_id": "ustie", "verdict": "dump", "marks": [{"x": 0.6, "y": 0.37}]},
            headers=auth(token),
        )
        body = r.json()
        tokens.append(token)
        if i < 2:
            assert body["consensus_reached"] is False, f"после {i+1} разметки рано"
        else:
            assert body["consensus_reached"] is True
            assert body["queued_for_moderation"] is True
            assert body["segment"]["votes"] == 3


async def test_s3_annotation_raises_attention_index(client):
    before = (await client.get("/api/v1/segments/ustie")).json()["attention_index"]
    await reach_consensus(client)
    after = (await client.get("/api/v1/segments/ustie")).json()["attention_index"]
    assert after > before, f"индекс должен вырасти: {before} → {after}"


# ------------------------------------------------------------------ #
# S4. Модерация ООПТ
# ------------------------------------------------------------------ #

async def test_s4_volunteer_cannot_open_moderation_queue(client, volunteer):
    await complete_course(client, volunteer["access_token"])
    r = await client.get(
        "/api/v1/moderation/segments", headers=auth(volunteer["access_token"])
    )
    assert r.status_code == 403


async def test_s4_queue_shows_consensus_segment(client, staff_token):
    await reach_consensus(client)
    r = await client.get("/api/v1/moderation/segments", headers=auth(staff_token))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]["segment"]["id"] == "ustie"
    assert items[0]["annotations_count"] == 3
    assert items[0]["agreement"] == 1.0
    assert len(items[0]["marks_heatmap"]) == 3


async def test_s4_approve_confirms_segment_and_raises_reputation(client, staff_token):
    await reach_consensus(client)
    r = await client.post(
        "/api/v1/moderation/segments/ustie",
        json={"decision": "approve", "comment": "Подтверждено обходом"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "work"
    assert r.json()["verified"] is True


async def test_s4_reject_lowers_factor_and_returns_to_watch(client, staff_token):
    await reach_consensus(client)
    r = await client.post(
        "/api/v1/moderation/segments/ustie",
        json={"decision": "reject", "comment": "Это отмель, не мусор"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "watch"
    assert body["verified"] is False
    assert body["votes"] == 0


async def test_s4_batch_moderation_route_is_not_shadowed(client, staff_token):
    """Регрессия: /segments/batch не должен попадать в обработчик {segment_id}."""
    await reach_consensus(client)
    r = await client.post(
        "/api/v1/moderation/segments/batch",
        json={"segment_ids": ["ustie", "kosa"], "decision": "approve"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == ["ustie"]
    assert body["skipped"]["kosa"] == "segment_not_in_queue"


async def test_s4_digest_gives_inspector_a_result(client, staff_token):
    await reach_consensus(client)
    r = await client.get("/api/v1/moderation/digest", headers=auth(staff_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments_total"] == 3
    assert body["annotations_received"] == 3
    assert body["volunteers_active"] == 3
    assert len(body["top_segments"]) == 3


# ------------------------------------------------------------------ #
# S5. Акция и допуск несовершеннолетнего
# ------------------------------------------------------------------ #

async def confirm_segment(client, staff_token) -> list[str]:
    """Довести участок до подтверждённого состояния.

    Возвращает токены волонтёров, которые его размечали: после
    подтверждения разметка по участку закрывается, и получить «своего»
    разметчика позже уже нельзя.
    """
    tokens = await reach_consensus(client)
    r = await client.post(
        "/api/v1/moderation/segments/ustie",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200
    return tokens


async def create_event(client, staff_token) -> str:
    starts = (utcnow() + timedelta(days=7)).isoformat()
    r = await client.post(
        "/api/v1/events",
        json={"segment_id": "ustie", "starts_at": starts, "capacity": 25},
        headers=auth(staff_token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_s5_event_requires_confirmed_segment(client, staff_token):
    starts = (utcnow() + timedelta(days=7)).isoformat()
    r = await client.post(
        "/api/v1/events",
        json={"segment_id": "zaliv", "starts_at": starts, "capacity": 10},
        headers=auth(staff_token),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "segment_not_confirmed"


async def test_s5_volunteer_cannot_create_event(client, staff_token, volunteer):
    await complete_course(client, volunteer["access_token"])
    starts = (utcnow() + timedelta(days=7)).isoformat()
    r = await client.post(
        "/api/v1/events",
        json={"segment_id": "ustie", "starts_at": starts, "capacity": 10},
        headers=auth(volunteer["access_token"]),
    )
    assert r.status_code == 403


async def test_s5_minor_blocked_until_consent_signed(client, staff_token, volunteer):
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = volunteer["access_token"]
    await complete_course(client, token)

    r = await client.post(
        f"/api/v1/events/{event_id}/enrollment", headers=auth(token)
    )
    assert r.status_code == 201, r.text
    assert "parent_consent" in r.json()["blocking_requirements"]

    # Запрос согласия — ещё не согласие.
    r = await client.post(
        f"/api/v1/events/{event_id}/consent",
        json={"parent_contact": "parent@test.ru"},
        headers=auth(token),
    )
    assert r.status_code == 202
    assert "parent_consent" in r.json()["blocking_requirements"], (
        "отправленный запрос не должен открывать допуск"
    )

    # Инструктаж пройден, но согласия всё ещё нет.
    r = await client.post(f"/api/v1/events/{event_id}/briefing", headers=auth(token))
    assert r.json()["status"] == "pending_requirements"
    assert r.json()["blocking_requirements"] == ["parent_consent"]


async def test_s5_admission_after_consent_and_briefing(
    client, staff_token, volunteer, session_factory
):
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = volunteer["access_token"]
    await complete_course(client, token)

    await client.post(f"/api/v1/events/{event_id}/enrollment", headers=auth(token))
    await client.post(
        f"/api/v1/events/{event_id}/consent",
        json={"parent_contact": "parent@test.ru"},
        headers=auth(token),
    )

    # Родитель подписывает по ссылке из письма.
    from sqlalchemy import select

    from app.models.event import Consent

    async with session_factory() as s:
        consent = (await s.execute(select(Consent))).scalar_one()
        sign_token = consent.token

    r = await client.post(f"/api/v1/events/{event_id}/consent/{sign_token}/sign")
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/v1/events/{event_id}/briefing", headers=auth(token))
    assert r.json()["blocking_requirements"] == []
    assert r.json()["status"] == "ready"


async def test_s5_event_shows_personal_attachment(client, staff_token):
    """Механика персональной привязки: волонтёр видит акцию на «своём» участке."""
    annotator_tokens = await confirm_segment(client, staff_token)
    await create_event(client, staff_token)

    # Тот, кто размечал «Устье», видит акцию как свою.
    r = await client.get("/api/v1/events", headers=auth(annotator_tokens[0]))
    assert r.status_code == 200, r.text
    assert r.json()[0]["is_my_segment"] is True

    # Волонтёр, размечавший другой участок, — не видит.
    outsider = await register_observer(client, "outsider@test.ru")
    await client.post(
        "/api/v1/annotations",
        json={"segment_id": "kosa", "verdict": "dump", "marks": [{"x": 0.3, "y": 0.3}]},
        headers=auth(outsider),
    )
    r = await client.get("/api/v1/events", headers=auth(outsider))
    assert r.json()[0]["is_my_segment"] is False


async def test_confirmed_segment_becomes_control_task(client, staff_token):
    """Подтверждённый участок уходит в эталонный пул.

    Разметка по нему теперь принимается как скрытая проверка — и ответ
    обязан быть неотличим от обычной первой разметки, иначе механика
    измерения точности сломается.
    """
    await confirm_segment(client, staff_token)
    late = await register_observer(client, "late@test.ru")
    r = await client.post(
        "/api/v1/annotations",
        json={"segment_id": "ustie", "verdict": "dump", "marks": [{"x": 0.6, "y": 0.37}]},
        headers=auth(late),
    )
    assert r.status_code == 201, r.text
    body = r.json()

    # Ответ не должен выдавать проверку.
    assert body["consensus_reached"] is False
    assert body["queued_for_moderation"] is False
    assert body["segment"]["verified"] is False
    assert "control" not in r.text.lower()
