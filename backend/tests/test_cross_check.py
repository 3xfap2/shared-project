"""Сверка бэкенда с эталонным прототипом.

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ. Проект заявляет, что две независимые
реализации — серверная и прототип — считают индекс участка одинаково.
Это утверждение вынесено в паспорт, README и презентацию, поэтому оно
обязано быть проверяемым автоматически.

Предыдущая версия проверки была негодной: тест задавал `factor_c = 0.73`
руками — значение, которое код консенсуса не выдаёт никогда — и потому
проверял арифметику весов, а не совпадение систем. Расхождение между
бэкендом и прототипом он не поймал.

Здесь консенсус прогоняется через настоящий API: три волонтёра размечают
участок, инспектор подтверждает, волонтёр сдаёт отчёт, инспектор его
принимает. Числа сравниваются с теми, что печатает prototype/e2e-check.js.

Если поменяется формула на любой из сторон — этот тест упадёт.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth
from tests.test_api_flow import reach_consensus
from tests.test_s6_cycle import submit_full_report

pytestmark = pytest.mark.asyncio

# Эталонные значения. Те же числа печатает `node prototype/e2e-check.js`
# и проверяет там же. Менять только вместе с прототипом.
INDEX_SEEDED = 71        # исходное состояние после сидов
INDEX_AFTER_CONSENSUS = 85   # три согласных разметки
INDEX_AFTER_CLEANUP = 48     # подтверждённая уборка


async def test_seeded_index_matches_prototype(client):
    """Исходное состояние участка совпадает в обеих реализациях."""
    body = (await client.get("/api/v1/segments/ustie")).json()
    assert body["attention_index"] == INDEX_SEEDED, (
        f"сиды разошлись с прототипом: {body['attention_index']} вместо {INDEX_SEEDED}"
    )


async def test_index_after_consensus_matches_prototype(client):
    """Три согласных разметки дают тот же индекс, что и прототип.

    Консенсус здесь настоящий: три отдельных пользователя проходят курс
    и отправляют разметку через API. Никаких подставленных факторов.
    """
    await reach_consensus(client)
    body = (await client.get("/api/v1/segments/ustie")).json()

    assert body["votes"] == 3
    assert body["factors"]["c"] == pytest.approx(1.0), (
        "при полном согласии трёх разметчиков фактор C равен единице"
    )
    assert body["attention_index"] == INDEX_AFTER_CONSENSUS, (
        f"бэкенд даёт {body['attention_index']}, прототип — {INDEX_AFTER_CONSENSUS}. "
        "Реализации разошлись: проверьте services/consensus.py и app.js"
    )


async def test_index_after_cleanup_matches_prototype(client, staff_token):
    """Подтверждённая уборка даёт тот же индекс, что и прототип."""
    report_id, _, index_before = await submit_full_report(client, staff_token)
    assert index_before == INDEX_AFTER_CONSENSUS

    r = await client.post(
        f"/api/v1/moderation/reports/{report_id}",
        json={"decision": "approve"},
        headers=auth(staff_token),
    )
    assert r.status_code == 200, r.text

    body = (await client.get("/api/v1/segments/ustie")).json()
    assert body["factors"]["t"] == 0.0, "давность обнуляется полностью, без остатка"
    assert body["attention_index"] == INDEX_AFTER_CLEANUP, (
        f"бэкенд даёт {body['attention_index']}, прототип — {INDEX_AFTER_CLEANUP}"
    )


async def test_single_annotation_does_not_max_out_consensus(client):
    """Одна разметка — это не консенсус.

    Регрессия на реальный дефект: фактор C считался как чистая доля
    согласия, поэтому единственный голос «мусор» давал C = 1.0 и в
    одиночку поднимал участок наверх карты приоритетов. Это обходило
    саму идею консенсуса — ради которой инспектор системе и доверяет.
    """
    from tests.test_api_flow import register_observer

    before = (await client.get("/api/v1/segments/kosa")).json()

    token = await register_observer(client, "single@test.ru")
    r = await client.post(
        "/api/v1/annotations",
        json={"segment_id": "kosa", "verdict": "dump", "marks": [{"x": 0.3, "y": 0.3}]},
        headers=auth(token),
    )
    assert r.status_code == 201

    after = (await client.get("/api/v1/segments/kosa")).json()
    assert after["factors"]["c"] == pytest.approx(1 / 3), (
        "одна разметка из трёх требуемых даёт треть фактора, а не единицу"
    )
    assert after["attention_index"] < INDEX_AFTER_CONSENSUS
    assert after["votes"] == 1
    assert r.json()["consensus_reached"] is False
