"""Игровой контур и фильтры карты."""

from __future__ import annotations

import pytest

from app.models.enums import Verdict
from app.models.geo import Segment
from app.services import games
from tests.conftest import auth
from tests.test_api_flow import confirm_segment, reach_consensus, register_observer

pytestmark = pytest.mark.asyncio


# ================================================================== #
# Начисление очков — чистая логика
# ================================================================== #

def test_agreement_outweighs_speed():
    """Игра, где выигрывает самый быстрый, учит щёлкать не глядя.
    А разметка из игры идёт в тот же консенсус, что и обычная."""
    fast_but_wrong = games.score_duel("dump", 1000, "none", 50_000)
    slow_but_agreed = games.score_duel("dump", 200_000, "dump", 200_001)

    assert fast_but_wrong.score_a < slow_but_agreed.score_a, (
        "согласие должно давать больше, чем скорость"
    )
    assert fast_but_wrong.agreed is False
    assert slow_but_agreed.agreed is True


def test_verdict_type_mismatch_still_counts_as_agreement():
    """Спутать свалку с общей замусоренностью простительно: решение
    инспектора от этого не меняется. Та же мера, что в контрольных."""
    r = games.score_duel("dump", 5000, "litter", 6000)
    assert r.agreed is True


def test_implausible_speed_gets_no_bonus():
    """Время меряет клиент. Занизить его тривиально, поэтому заведомо
    невозможный результат бонуса не даёт."""
    cheated = games.score_duel("dump", 5, "dump", 9000)
    assert cheated.winner_index == 1, "бонус ушёл честному игроку"


def test_abandoned_move_gets_no_speed_bonus():
    r = games.score_duel("dump", 9_000_000, "dump", 4000)
    assert r.winner_index == 1


def test_participation_points_for_the_loser():
    """Проигравший с нулём больше не возвращается."""
    r = games.score_duel("dump", 3000, "none", 4000)
    assert r.score_a > 0 and r.score_b > 0


def test_inspector_accuracy_hidden_until_enough_rounds():
    assert games.inspector_accuracy(0, 1) is None
    assert games.inspector_accuracy(4, 5) == pytest.approx(0.8)


# ================================================================== #
# Дуэль через API
# ================================================================== #

async def test_student_cannot_play(client, volunteer):
    """Пока курс не пройден, ход не имеет ценности ни для игры,
    ни для системы."""
    r = await client.post(
        "/api/v1/games/duels/join", headers=auth(volunteer["access_token"])
    )
    assert r.status_code == 403
    assert r.json()["code"] == "course_not_completed"


async def test_second_player_joins_the_open_duel(client):
    a = await register_observer(client, "duel-a@test.ru")
    b = await register_observer(client, "duel-b@test.ru")

    first = await client.post("/api/v1/games/duels/join", headers=auth(a))
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "open"
    assert first.json()["opponent_joined"] is False

    second = await client.post("/api/v1/games/duels/join", headers=auth(b))
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"], "должны попасть в одну дуэль"
    assert second.json()["status"] == "active"
    assert second.json()["opponent_joined"] is True


async def test_opponent_move_is_hidden_until_both_played(client):
    """Иначе игра превращается в списывание, а разметки теряют
    независимость — и вместе с ней ценность для консенсуса."""
    a = await register_observer(client, "hide-a@test.ru")
    b = await register_observer(client, "hide-b@test.ru")

    duel_id = (await client.post("/api/v1/games/duels/join", headers=auth(a))).json()["id"]
    await client.post("/api/v1/games/duels/join", headers=auth(b))

    await client.post(
        f"/api/v1/games/duels/{duel_id}/move",
        json={"verdict": "dump", "marks": [{"x": 0.6, "y": 0.4}], "elapsed_ms": 4000},
        headers=auth(a),
    )

    view = await client.get(f"/api/v1/games/duels/{duel_id}", headers=auth(b))
    body = view.json()
    assert body["opponent_move_done"] is True
    assert body["opponent_score"] is None, "очки соперника раскрывают его ход"
    assert body["agreed"] is None


async def play_duel(client, verdict_a="dump", verdict_b="dump") -> dict:
    a = await register_observer(client, f"p-{verdict_a}-a@test.ru")
    b = await register_observer(client, f"p-{verdict_b}-b@test.ru")

    duel_id = (await client.post("/api/v1/games/duels/join", headers=auth(a))).json()["id"]
    await client.post("/api/v1/games/duels/join", headers=auth(b))

    marks = [{"x": 0.6, "y": 0.4}]
    await client.post(
        f"/api/v1/games/duels/{duel_id}/move",
        json={"verdict": verdict_a, "marks": marks if verdict_a != "none" else [],
              "elapsed_ms": 4000},
        headers=auth(a),
    )
    last = await client.post(
        f"/api/v1/games/duels/{duel_id}/move",
        json={"verdict": verdict_b, "marks": marks if verdict_b != "none" else [],
              "elapsed_ms": 9000},
        headers=auth(b),
    )
    return {"duel_id": duel_id, "a": a, "b": b, "last": last.json()}


async def test_duel_finishes_and_scores(client):
    game = await play_duel(client)
    body = game["last"]

    assert body["status"] == "finished"
    assert body["agreed"] is True
    assert body["my_score"] is not None
    assert body["opponent_score"] is not None
    assert body["result"] in ("win", "lose", "draw")


async def test_duel_move_becomes_real_annotation(client):
    """Главное свойство: игра производит настоящие данные.

    Два независимых мнения по участку — это уже две трети порога
    консенсуса, а не отдельный игровой контур, которому пришлось бы
    доверять меньше.
    """
    game = await play_duel(client)
    segment_id = (
        await client.get(f"/api/v1/games/duels/{game['duel_id']}", headers=auth(game["a"]))
    ).json()["segment_id"]

    segment = (await client.get(f"/api/v1/segments/{segment_id}")).json()
    assert segment["votes"] == 2, "оба хода стали разметками"
    assert segment["factors"]["c"] == pytest.approx(2 / 3)


async def test_double_move_rejected(client):
    a = await register_observer(client, "double-a@test.ru")
    duel_id = (await client.post("/api/v1/games/duels/join", headers=auth(a))).json()["id"]

    move = {"verdict": "dump", "marks": [{"x": 0.5, "y": 0.5}], "elapsed_ms": 3000}
    first = await client.post(
        f"/api/v1/games/duels/{duel_id}/move", json=move, headers=auth(a)
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/games/duels/{duel_id}/move", json=move, headers=auth(a)
    )
    assert second.status_code == 409
    assert second.json()["code"] == "move_already_made"


async def test_outsider_cannot_view_duel(client):
    a = await register_observer(client, "own-a@test.ru")
    duel_id = (await client.post("/api/v1/games/duels/join", headers=auth(a))).json()["id"]

    stranger = await register_observer(client, "nosy@test.ru")
    r = await client.get(f"/api/v1/games/duels/{duel_id}", headers=auth(stranger))
    assert r.status_code == 403


# ================================================================== #
# «Инспектор на день»
# ================================================================== #

async def test_inspector_case_hides_the_real_decision(client, staff_token):
    await confirm_segment(client, staff_token)
    player = await register_observer(client, "insp@test.ru")

    r = await client.get("/api/v1/games/inspector/case", headers=auth(player))
    assert r.status_code == 200, r.text
    body = r.text.lower()
    assert "actual_decision" not in body
    assert "control_truth" not in body


async def test_inspector_answer_reveals_truth_and_scores(client, staff_token):
    await confirm_segment(client, staff_token)
    player = await register_observer(client, "insp2@test.ru")

    case = (await client.get("/api/v1/games/inspector/case", headers=auth(player))).json()
    r = await client.post(
        "/api/v1/games/inspector/answer",
        json={"segment_id": case["segment_id"], "decision": "approve"},
        headers=auth(player),
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["actual_decision"] == "approve", "инспектор этот участок подтвердил"
    assert body["correct"] is True
    assert body["points"] == games.POINTS_INSPECTOR_CORRECT
    assert body["explanation_key"].startswith("game.inspector.why.")
    assert body["rounds_played"] == 1
    assert body["accuracy"] is None, "по одному раунду точность не показываем"


async def test_case_cannot_be_replayed(client, staff_token):
    """Иначе игрок переигрывает тот же случай и статистика ничего не значит."""
    await confirm_segment(client, staff_token)
    player = await register_observer(client, "replay@test.ru")

    case = (await client.get("/api/v1/games/inspector/case", headers=auth(player))).json()
    payload = {"segment_id": case["segment_id"], "decision": "reject"}

    assert (await client.post("/api/v1/games/inspector/answer", json=payload,
                              headers=auth(player))).status_code == 201
    again = await client.post("/api/v1/games/inspector/answer", json=payload,
                              headers=auth(player))
    assert again.status_code == 409
    assert again.json()["code"] == "case_already_played"


async def test_inspector_game_does_not_touch_segment(client, staff_token):
    await confirm_segment(client, staff_token)
    before = (await client.get("/api/v1/segments/ustie")).json()

    player = await register_observer(client, "safe@test.ru")
    case = (await client.get("/api/v1/games/inspector/case", headers=auth(player))).json()
    await client.post(
        "/api/v1/games/inspector/answer",
        json={"segment_id": case["segment_id"], "decision": "reject"},
        headers=auth(player),
    )

    after = (await client.get("/api/v1/segments/ustie")).json()
    assert after["attention_index"] == before["attention_index"]
    assert after["status"] == before["status"]


# ================================================================== #
# Рейтинг
# ================================================================== #

async def test_leaderboard_hides_minor_names(client):
    """Профили 14–17 закрыты, и рейтинг не повод их раскрывать."""
    minor = await register_observer(client, "kid@test.ru", age=16)
    duel_id = (await client.post("/api/v1/games/duels/join", headers=auth(minor))).json()["id"]
    await client.post(
        f"/api/v1/games/duels/{duel_id}/move",
        json={"verdict": "dump", "marks": [{"x": 0.5, "y": 0.5}], "elapsed_ms": 3000},
        headers=auth(minor),
    )

    r = await client.get("/api/v1/games/leaderboard", headers=auth(minor))
    assert r.status_code == 200
    names = [e["display_name"] for e in r.json()["entries"]]
    assert all("kid@test.ru" not in n for n in names)
    assert any(n.startswith("Наблюдатель #") for n in names)


async def test_stats_aggregate_both_games(client, staff_token):
    await confirm_segment(client, staff_token)
    player = await register_observer(client, "stats@test.ru")

    case = (await client.get("/api/v1/games/inspector/case", headers=auth(player))).json()
    await client.post(
        "/api/v1/games/inspector/answer",
        json={"segment_id": case["segment_id"], "decision": "approve"},
        headers=auth(player),
    )

    r = await client.get("/api/v1/games/stats", headers=auth(player))
    assert r.status_code == 200
    body = r.json()
    assert body["inspector_rounds"] == 1
    assert body["total_points"] > 0


# ================================================================== #
# Фильтры карты
# ================================================================== #

async def test_map_filters_by_region(client):
    ok = await client.get("/api/v1/segments?region=Тестовый край")
    assert ok.status_code == 200
    assert ok.json()["total"] == 3

    empty = await client.get("/api/v1/segments?region=Другая область")
    assert empty.json()["total"] == 0


async def test_map_filters_by_incident_type(client):
    """Свалка и общая замусоренность — разные бригады и разная техника,
    инспектор ищет их по отдельности."""
    await reach_consensus(client)

    dumps = await client.get("/api/v1/segments?incident=dump")
    assert dumps.status_code == 200
    ids = [s["id"] for s in dumps.json()["items"]]
    assert "ustie" in ids

    litter = await client.get("/api/v1/segments?incident=litter")
    assert "ustie" not in [s["id"] for s in litter.json()["items"]]


async def test_map_filters_by_growth(client, session_factory):
    async with session_factory() as s:
        seg = await s.get(Segment, "kosa")
        seg.growth_rate = 0.4
        await s.commit()

    growing = await client.get("/api/v1/segments?growing=true")
    assert [s["id"] for s in growing.json()["items"]] == ["kosa"]

    stable = await client.get("/api/v1/segments?growing=false")
    assert "kosa" not in [s["id"] for s in stable.json()["items"]]


async def test_map_filters_by_viewport(client, session_factory):
    """Фильтр по видимой области карты — первый пространственный запрос."""
    async with session_factory() as s:
        for sid, lat, lon in (("ustie", 61.24, 30.11), ("kosa", 61.29, 30.06),
                              ("zaliv", 55.75, 37.62)):
            seg = await s.get(Segment, sid)
            seg.center_lat, seg.center_lon = lat, lon
        await s.commit()

    karelia = await client.get("/api/v1/segments?bbox=29.8,61.0,30.4,61.4")
    ids = sorted(s["id"] for s in karelia.json()["items"])
    assert ids == ["kosa", "ustie"], "участок под Москвой не должен попасть"


async def test_malformed_bbox_is_client_error(client):
    for bad in ("1,2,3", "a,b,c,d", "30,61,29,62", "200,61,201,62"):
        r = await client.get(f"/api/v1/segments?bbox={bad}")
        assert r.status_code == 422, f"bbox={bad}"
        assert r.json()["code"].startswith("bbox_")


async def test_filters_combine(client, session_factory):
    await reach_consensus(client)
    async with session_factory() as s:
        seg = await s.get(Segment, "ustie")
        seg.center_lat, seg.center_lon = 61.24, 30.11
        await s.commit()

    r = await client.get(
        "/api/v1/segments?region=Тестовый край&incident=dump&bbox=29.8,61.0,30.4,61.4"
    )
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["items"]] == ["ustie"]
