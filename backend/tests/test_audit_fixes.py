"""Регрессии по находкам независимого аудита.

Каждый тест закрывает конкретный дефект, найденный внешней проверкой.
Без них правки живут ровно до следующего рефакторинга.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.config import Settings, settings
from app.db.base import utcnow
from app.models.user import User
from tests.conftest import auth
from tests.test_api_flow import (
    complete_course,
    confirm_segment,
    create_event,
    register_observer,
)
from tests.test_s6_cycle import admitted_volunteer, submit_full_report, upload_photo

pytestmark = pytest.mark.asyncio


# ═══════════════ К3. Округление возраста ═══════════════

def test_minor_detection_errs_towards_child():
    """Зарегистрировавшийся в 17 не должен становиться взрослым в январе.

    Год рождения даёт возраст с точностью ±1: день рождения в текущем году
    мог ещё не наступить. Раньше система считала по разнице годов и почти
    на год раньше срока переставала требовать согласие родителя —
    несовершеннолетний выходил на охраняемую территорию без разрешения
    законного представителя.
    """
    year = date.today().year

    # Зарегистрировался в 17 лет: birth_year = текущий год − 17.
    seventeen = User(email="a@b.ru", password_hash="x", name="Т",
                     birth_year=year - 17)
    assert seventeen.is_minor is True

    # На следующий год разница годов = 18, но реально может быть ещё 17.
    next_year = User(email="c@d.ru", password_hash="x", name="Т",
                     birth_year=year - 18)
    assert next_year.is_minor is True, (
        "разница годов 18 не гарантирует совершеннолетия — округляем в "
        "сторону «ещё ребёнок»"
    )

    # Разница 19 — восемнадцать исполнилось при любом дне рождения.
    adult = User(email="e@f.ru", password_hash="x", name="Т",
                 birth_year=year - 19)
    assert adult.is_minor is False


# ═══════════════ К2. Накрутка часов повторными отчётами ═══════════════

async def test_second_report_on_same_event_rejected(client, staff_token):
    """Один отчёт на участника и акцию.

    Раньше волонтёр сдавал сколько угодно отчётов по одному выезду: каждое
    подтверждение начисляло 4 часа заново и повторно топило участок в конце
    карты приоритетов.
    """
    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)
    token = await admitted_volunteer(client, staff_token, event_id)

    photo = await upload_photo(client, token)
    first = await client.post(
        "/api/v1/reports",
        json={"event_id": event_id, "photo_before_id": photo},
        headers=auth(token),
    )
    assert first.status_code == 201

    second_photo = await upload_photo(client, token)
    second = await client.post(
        "/api/v1/reports",
        json={"event_id": event_id, "photo_before_id": second_photo},
        headers=auth(token),
    )
    assert second.status_code == 409, "второй отчёт по той же акции недопустим"


async def test_cleanup_applied_once_per_segment(client, staff_token):
    """Повторное подтверждение не снижает индекс во второй раз."""
    report_id, _, _ = await submit_full_report(client, staff_token)

    r = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )
    after_first = r.json()["attention_index_after"]

    repeat = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )
    assert repeat.status_code == 409, "уже обработанный отчёт повторно не решается"

    body = (await client.get("/api/v1/segments/ustie")).json()
    assert body["attention_index"] == after_first


# ═══════════════ К4. Перебор ответов курса ═══════════════

async def test_course_cannot_be_brute_forced(client, volunteer):
    """Курс — условие допуска в поле, а не формальность."""
    token = volunteer["access_token"]
    codes = []
    for _ in range(settings.MAX_MODULE_ATTEMPTS + 3):
        r = await client.post(
            "/api/v1/course/modules/m1/answer",
            json={"answer_index": 0},          # заведомо неверный
            headers=auth(token),
        )
        codes.append(r.status_code)

    assert 429 in codes, f"перебор ответов не ограничен: {codes}"
    blocked = await client.post(
        "/api/v1/course/modules/m1/answer",
        json={"answer_index": 1},              # даже верный уже не принимается
        headers=auth(token),
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "module_attempts_exhausted"


# ═══════════════ К5. Обход ограничения частоты заголовком ═══════════════

async def test_forwarded_header_does_not_bypass_rate_limit(client, volunteer):
    """X-Forwarded-For от недоверенного источника игнорируется.

    Раньше заголовку доверяли безусловно: клиент подставлял произвольный
    адрес, счётчик заводился заново, и ограничение обходилось целиком.
    """
    codes = []
    for i in range(10):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "anya@test.ru", "password": "wrong"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
        codes.append(r.status_code)

    assert 429 in codes, (
        f"подмена X-Forwarded-For обошла ограничение: {codes}"
    )


def test_forwarded_header_honoured_from_trusted_proxy():
    """От доверенного прокси заголовок принимается — иначе за nginx все
    клиенты считались бы одним и лимит выбивал бы всех разом."""
    from app.core.ratelimit import client_key

    class _Req:
        def __init__(self, peer, headers):
            self.client = type("C", (), {"host": peer})()
            self.headers = headers

    untrusted = _Req("203.0.113.7", {"x-forwarded-for": "1.2.3.4"})
    assert client_key(untrusted) == "203.0.113.7"

    trusted_settings = Settings(TRUSTED_PROXIES=["10.0.0.1"])
    import app.core.ratelimit as rl

    original = rl.settings
    rl.settings = trusted_settings
    try:
        trusted = _Req("10.0.0.1", {"x-forwarded-for": "1.2.3.4, 10.0.0.1"})
        assert client_key(trusted) == "1.2.3.4"
    finally:
        rl.settings = original


# ═══════════════ В1. Секреты в журнале ═══════════════

def test_consent_token_and_child_name_are_not_logged():
    """Токен согласия из лога позволял подписать разрешение вместо родителя."""
    from app.services.notifications import _safe_vars

    safe = _safe_vars({
        "child": "Анна Сергеевна Иванова",
        "url": "/consent/kZ9x_SECRET_TOKEN_abc123",
        "segment": "seg.ustie.name",
    })

    assert safe["url"] == "<скрыто>"
    assert "SECRET_TOKEN" not in str(safe)
    assert "Сергеевна" not in safe["child"], "ФИО подростка не пишем в журнал"
    assert safe["segment"] == "seg.ustie.name", "служебные ключи не маскируем"


# ═══════════════ В4. Формат ошибок валидации ═══════════════

async def test_validation_errors_follow_contract(client, volunteer):
    """Все ошибки — в одном формате, иначе фронт падает на body['code']."""
    await complete_course(client, volunteer["access_token"])
    r = await client.post(
        "/api/v1/annotations",
        json={"segment_id": "ustie", "verdict": "dump", "marks": []},
        headers=auth(volunteer["access_token"]),
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message_key"] == "err.validation_error"
    assert "fields" in body["details"]


async def test_validation_errors_carry_no_russian_text(client):
    """Контракт обещает, что API не возвращает переведённых строк."""
    r = await client.post(
        "/api/v1/auth/register",
        json={"name": "  ", "email": "not-an-email", "password": "1234", "age": 5},
    )
    assert r.status_code == 422
    assert not any("Ѐ" <= ch <= "ӿ" for ch in r.text), (
        f"в ответе кириллица: {r.text[:200]}"
    )


# ═══════════════ В7. Детектор роста подключён к системе ═══════════════

async def test_growth_detector_is_wired_to_api(client, session_factory):
    """Фича заявлена в паспорте — значит должна работать на живой системе.

    До этой правки apply_growth вызывался только из тестов: growth_rate
    всегда оставался None, а бейдж «растёт» не появлялся никогда.
    """
    from app.core.security import hash_password
    from app.models.enums import Role

    async with session_factory() as s:
        s.add(User(email="admin@t.ru", password_hash=hash_password("Admin-123"),
                   name="Админ", birth_year=1990, role=Role.ADMIN))
        await s.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@t.ru", "password": "Admin-123"},
    )
    token = login.json()["access_token"]

    base = {
        "source": "resurs-p",
        "tile_url_template": "https://t/{z}/{x}/{y}.png",
        "resolution_m": 1.0,
    }

    first = await client.post(
        "/api/v1/segments/zaliv/scenes",
        json={**base, "captured_at": "2026-05-01", "anomaly_area_m2": 400.0},
        headers=auth(token),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/segments/zaliv/scenes",
        json={**base, "captured_at": "2026-07-01", "anomaly_area_m2": 700.0},
        headers=auth(token),
    )
    assert second.status_code == 201, second.text
    body = second.json()

    assert body["growth_rate"] == pytest.approx(0.75)
    assert body["is_growing"] is True
    assert body["attention_index_after"] > body["attention_index_before"]

    public = (await client.get("/api/v1/segments/zaliv")).json()
    assert public["is_growing"] is True, "фронт обязан увидеть бейдж «растёт»"


async def test_growing_segment_gets_urgent_imagery_priority(client, staff_token, session_factory):
    """Ветка автоповышения срочности перестала быть мёртвой."""
    from app.models.geo import Segment

    async with session_factory() as s:
        seg = await s.get(Segment, "kosa")
        seg.growth_rate = 0.5
        await s.commit()

    r = await client.post(
        "/api/v1/segments/kosa/imagery-requests",
        json={"priority": "normal"},
        headers=auth(staff_token),
    )
    assert r.status_code == 201
    assert r.json()["priority"] == "urgent"


# ═══════════════ Прочие находки ═══════════════

async def test_cannot_enrol_in_past_event(client, staff_token, session_factory):
    """Статуса PLANNED мало: акция могла просто пройти."""
    from app.models.event import Event

    await confirm_segment(client, staff_token)
    event_id = await create_event(client, staff_token)

    async with session_factory() as s:
        event = await s.get(Event, __import__("uuid").UUID(event_id))
        event.starts_at = utcnow() - timedelta(days=1)
        await s.commit()

    token = await register_observer(client, "late-bird@test.ru")
    r = await client.post(
        f"/api/v1/events/{event_id}/enrollment", headers=auth(token)
    )
    assert r.status_code == 409
    assert r.json()["code"] == "event_already_started"


async def test_minor_surname_not_exposed_to_moderator(client, staff_token, session_factory):
    """Раньше показывали первое слово имени — при порядке «Фамилия Имя»
    это раскрывало ровно фамилию подростка."""
    from app.models.enums import Role
    from app.models.user import User as U
    from sqlalchemy import select

    report_id, _, _ = await submit_full_report(client, staff_token)

    # Делаем автора отчёта несовершеннолетним с ФИО в отчётном порядке.
    async with session_factory() as s:
        author = (
            await s.execute(select(U).where(U.email == "field@test.ru"))
        ).scalar_one()
        author.name = "Тестова Аня Сергеевна"
        author.birth_year = date.today().year - 16
        await s.commit()

    r = await client.get(f"/api/v1/reports/{report_id}", headers=auth(staff_token))
    assert r.status_code == 200
    shown = r.json()["author_name"]
    assert "Тестова" not in shown
    assert "Сергеевна" not in shown
    assert shown.startswith("Наблюдатель #")


def test_secret_key_placeholder_rejected_in_production():
    """Валидатор ловил одну конкретную строку, а .env.example предлагал
    другую — защита обходилась подсказкой самого проекта."""
    for weak in ("change-me-in-production", "dev-only-insecure-key-change-me", "short"):
        with pytest.raises(Exception):
            Settings(ENV="production", SECRET_KEY=weak)

    ok = Settings(ENV="production", SECRET_KEY="a" * 48)
    assert ok.SECRET_KEY


def test_naming_convention_is_actually_applied():
    """Словарь лежал в атрибуте, которого SQLAlchemy не знает, и молча
    игнорировался — заявленные стабильные имена не работали."""
    from app.db.base import Base

    assert Base.metadata.naming_convention.get("uq") == (
        "uq_%(table_name)s_%(column_0_name)s"
    )
