"""Тесты трёх согласованных фишек.

1. Скрытые эталонные задания — калибровка разметчиков.
2. Заявка на новую съёмку через оператора ДЗЗ.
3. Детектор растущей аномалии.
"""

from __future__ import annotations

import random
from datetime import date

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.enums import ImageryPriority, ImageryRequestStatus, SceneSource, Verdict
from app.models.geo import Scene, Segment
from app.models.user import User
from app.services import calibration, growth
from tests.conftest import auth
from tests.test_api_flow import (
    confirm_segment,
    reach_consensus,
    register_observer,
)

pytestmark = pytest.mark.asyncio


# ================================================================== #
# Фишка 1. Скрытые эталонные задания
# ================================================================== #

def test_control_check_compares_problem_presence_not_exact_type():
    """Спутать скопление отходов с замусоренностью — простительно:
    решение инспектора от этого не меняется. Пропустить проблему — нет."""
    seg = Segment(id="x", oopt_id="o", name_key="k", control_truth=Verdict.DUMP)

    assert calibration.check_answer(seg, Verdict.DUMP) is True
    assert calibration.check_answer(seg, Verdict.LITTER) is True, "тип уточнять не требуем"
    assert calibration.check_answer(seg, Verdict.NONE) is False, "проблему пропустил"

    clean = Segment(id="y", oopt_id="o", name_key="k", control_truth=Verdict.NONE)
    assert clean.control_truth is Verdict.NONE
    assert calibration.check_answer(clean, Verdict.NONE) is True
    assert calibration.check_answer(clean, Verdict.DUMP) is False, "выдумал проблему"


def test_truth_follows_inspector_decision():
    assert calibration.truth_from_decision(
        approved=True, prevailing=Verdict.DUMP
    ) is Verdict.DUMP
    assert calibration.truth_from_decision(
        approved=False, prevailing=Verdict.DUMP
    ) is Verdict.NONE, "инспектор проверил и не нашёл проблемы"


def test_accuracy_hidden_until_enough_samples():
    user = User(email="a@b.ru", password_hash="x", name="Т", birth_year=2000)
    user.control_tasks_count = 0
    user.control_correct_count = 0
    assert user.accuracy is None, "новичку не показываем 0%"

    user.control_tasks_count = settings.MIN_CONTROL_TASKS_FOR_ACCURACY
    user.control_correct_count = 4
    assert user.accuracy == pytest.approx(4 / settings.MIN_CONTROL_TASKS_FOR_ACCURACY)


def test_reputation_blends_with_measured_accuracy():
    """Пока замеров мало — верим репутации. Когда накопились — точности."""
    user = User(email="a@b.ru", password_hash="x", name="Т", birth_year=2000)
    user.reputation = 1.0

    user.control_tasks_count = 0
    assert calibration.blend_reputation(user) == 1.0, "нет данных — репутация как есть"

    user.control_tasks_count = 40
    user.control_correct_count = 40
    high = calibration.blend_reputation(user)

    user.control_correct_count = 10
    low = calibration.blend_reputation(user)

    assert high > low, "точный разметчик должен весить больше неточного"
    assert settings.REPUTATION_MIN <= low <= settings.REPUTATION_MAX
    assert settings.REPUTATION_MIN <= high <= settings.REPUTATION_MAX


def test_control_probability_is_random_not_scheduled():
    """Предсказуемая частота вычисляется пользователями и обесценивает проверку."""
    rng = random.Random(42)
    draws = [calibration.should_serve_control(rng) for _ in range(2000)]
    share = sum(draws) / len(draws)
    assert 0.10 < share < 0.20, f"доля контрольных ~15%, получили {share:.2f}"


async def test_control_annotation_does_not_touch_segment(client, staff_token):
    """Контрольная разметка не меняет состояние участка: решение принято."""
    await confirm_segment(client, staff_token)
    before = (await client.get("/api/v1/segments/ustie")).json()

    token = await register_observer(client, "ctrl@test.ru")
    r = await client.post(
        "/api/v1/annotations",
        json={"segment_id": "ustie", "verdict": "dump", "marks": [{"x": 0.6, "y": 0.4}]},
        headers=auth(token),
    )
    assert r.status_code == 201

    after = (await client.get("/api/v1/segments/ustie")).json()
    assert after["attention_index"] == before["attention_index"]
    assert after["votes"] == before["votes"]
    assert after["status"] == before["status"]


async def test_control_result_lands_in_profile(client, staff_token):
    await confirm_segment(client, staff_token)
    token = await register_observer(client, "prof@test.ru")

    await client.post(
        "/api/v1/annotations",
        json={"segment_id": "ustie", "verdict": "dump", "marks": [{"x": 0.6, "y": 0.4}]},
        headers=auth(token),
    )
    me = (await client.get("/api/v1/auth/me", headers=auth(token))).json()
    assert me["control_tasks_count"] == 1
    assert me["accuracy"] is None, "одного задания мало для показа точности"


async def test_task_never_reveals_control_flag(client, staff_token):
    """Ключевое требование: признак проверки не покидает сервер."""
    await confirm_segment(client, staff_token)
    token = await register_observer(client, "hidden@test.ru")

    for _ in range(15):
        r = await client.get("/api/v1/annotations/task", headers=auth(token))
        if r.status_code != 200:
            continue
        body = r.text.lower()
        assert "control" not in body
        assert "truth" not in body
        assert "эталон" not in body


async def test_moderation_shows_annotator_accuracy(client, staff_token):
    await reach_consensus(client)
    r = await client.get("/api/v1/moderation/segments", headers=auth(staff_token))
    assert r.status_code == 200
    assert "annotators_accuracy" in r.json()[0]


# ================================================================== #
# Фишка 2. Заявка на новую съёмку
# ================================================================== #

async def test_imagery_request_created_by_staff(client, staff_token):
    r = await client.post(
        "/api/v1/segments/ustie/imagery-requests",
        json={"priority": "normal", "comment": "Данные старше двух месяцев"},
        headers=auth(staff_token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["segment_id"] == "ustie"
    assert body["segment_name_key"] == "seg.ustie.name"


async def test_volunteer_cannot_request_imagery(client, volunteer):
    r = await client.post(
        "/api/v1/segments/ustie/imagery-requests",
        json={"priority": "normal"},
        headers=auth(volunteer["access_token"]),
    )
    assert r.status_code == 403


async def test_duplicate_active_request_blocked(client, staff_token):
    """Две заявки на одну съёмку — лишние деньги и путаница у оператора."""
    first = await client.post(
        "/api/v1/segments/ustie/imagery-requests",
        json={"priority": "normal"},
        headers=auth(staff_token),
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/segments/ustie/imagery-requests",
        json={"priority": "normal"},
        headers=auth(staff_token),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "imagery_request_already_active"


async def test_requests_list_filtered_by_territory(client, staff_token):
    await client.post(
        "/api/v1/segments/ustie/imagery-requests",
        json={"priority": "urgent"},
        headers=auth(staff_token),
    )
    r = await client.get("/api/v1/imagery-requests", headers=auth(staff_token))
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["priority"] == "urgent"

    filtered = await client.get(
        "/api/v1/imagery-requests?status=delivered", headers=auth(staff_token)
    )
    assert filtered.json() == []


async def test_growing_segment_upgrades_priority(client, staff_token, session_factory):
    """Растущая аномалия — основание для срочной съёмки: пока идёт обычная
    очередь оператора, свалка успевает вырасти ещё."""
    async with session_factory() as s:
        seg = await s.get(Segment, "kosa")
        seg.growth_rate = 0.42
        await s.commit()

    r = await client.post(
        "/api/v1/segments/kosa/imagery-requests",
        json={"priority": "normal"},
        headers=auth(staff_token),
    )
    assert r.status_code == 201
    assert r.json()["priority"] == "urgent", "рост должен повышать срочность сам"


# ================================================================== #
# Фишка 3. Детектор растущей свалки
# ================================================================== #

def _scene(area: float | None, day: int) -> Scene:
    return Scene(
        segment_id="x",
        captured_at=date(2025, day, 1),
        source=SceneSource.RESURS_P,
        tile_url_template="t",
        anomaly_area_m2=area,
    )


def test_growth_needs_two_measurements():
    rate, a, b = growth.compute_growth([_scene(100.0, 1)])
    assert rate is None, "по одному замеру о динамике судить нельзя"


def test_growth_uses_two_latest_scenes():
    """Интересует текущая динамика, а не изменение за всё время наблюдения:
    свалка, выросшая год назад и с тех пор стабильная, не срочная."""
    scenes = [_scene(100.0, 1), _scene(500.0, 2), _scene(500.0, 3)]
    rate, a, b = growth.compute_growth(scenes)
    assert rate == pytest.approx(0.0), "между двумя последними роста нет"
    assert (a, b) == (500.0, 500.0)


def test_growth_rate_computed():
    rate, a, b = growth.compute_growth([_scene(100.0, 1), _scene(150.0, 2)])
    assert rate == pytest.approx(0.5)


def test_new_anomaly_from_zero_is_maximum_growth():
    rate, _, _ = growth.compute_growth([_scene(0.0, 1), _scene(80.0, 2)])
    assert rate == 1.0, "аномалия появилась там, где её не было"


def test_growth_boosts_signal_but_keeps_formula():
    """Рост усиливает S внутри конвейера — пятого слагаемого в формуле нет."""
    seg = Segment(id="x", oopt_id="o", name_key="k", factor_s=0.50)
    result = growth.apply_growth(seg, [_scene(100.0, 1), _scene(200.0, 2)])

    assert result.rate == pytest.approx(1.0)
    assert result.is_growing is True
    assert seg.factor_s > 0.50, "растущая аномалия усиливает сигнал"
    assert seg.factor_s <= 1.0


def test_small_change_is_not_growth():
    seg = Segment(id="x", oopt_id="o", name_key="k", factor_s=0.50)
    result = growth.apply_growth(seg, [_scene(100.0, 1), _scene(105.0, 2)])

    assert result.is_growing is False, "5% — шум измерения, а не рост"
    assert seg.factor_s == 0.50, "сигнал не трогаем"


def test_shrinking_anomaly_does_not_boost():
    seg = Segment(id="x", oopt_id="o", name_key="k", factor_s=0.50)
    growth.apply_growth(seg, [_scene(200.0, 1), _scene(100.0, 2)])
    assert seg.factor_s == 0.50
    assert seg.is_growing is False


async def test_growth_exposed_in_segment_api(client, session_factory):
    """Фронту нужны is_growing и growth_rate для бейджа «Растёт»."""
    async with session_factory() as s:
        seg = await s.get(Segment, "ustie")
        seg.growth_rate = 0.35
        await s.commit()

    body = (await client.get("/api/v1/segments/ustie")).json()
    assert body["is_growing"] is True
    assert body["growth_rate"] == pytest.approx(0.35)


async def test_growth_visible_in_priority_map(client, session_factory):
    async with session_factory() as s:
        seg = await s.get(Segment, "zaliv")
        seg.growth_rate = 0.60
        await s.commit()

    items = (await client.get("/api/v1/segments")).json()["items"]
    growing = [i for i in items if i["is_growing"]]
    assert len(growing) == 1
    assert growing[0]["id"] == "zaliv"
