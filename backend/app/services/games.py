"""Правила начисления очков в игровом контуре.

Логика вынесена отдельно и не зависит от базы: правила игры — это то,
что чаще всего просят поменять, и менять их должно быть безопасно.

ПОЧЕМУ СКОРОСТЬ ДАЁТ МЕНЬШЕ ОЧКОВ, ЧЕМ СОГЛАСИЕ. Время меряет клиент,
и занизить его тривиально. Но дело не только в накрутке: игра, где
выигрывает самый быстрый, учит щёлкать не глядя — а нам нужна точность,
потому что разметка идёт в тот же консенсус, что и обычная. Поэтому
согласие с соперником весит втрое больше скорости, а скорость вообще
не начисляется тому, кто ошибся.
"""

from __future__ import annotations

from dataclasses import dataclass

# Очки за участие: игрок дошёл до конца хода. Без этого проигравший
# уходит с нулём и больше не возвращается.
POINTS_PARTICIPATION = 10

# Согласие двух независимых разметчиков — главная ценность для системы.
POINTS_AGREEMENT = 30

# Скорость — приятный бонус, но не цель.
POINTS_SPEED = 10

# Ход дольше этого считается брошенным: очки за скорость не начисляются.
MAX_REASONABLE_MS = 5 * 60 * 1000

# Ход быстрее этого физически невозможен — снимок нужно хотя бы увидеть.
# Такое значение считаем подделкой и скорость не засчитываем.
MIN_PLAUSIBLE_MS = 800


@dataclass(frozen=True)
class DuelOutcome:
    score_a: int
    score_b: int
    agreed: bool
    winner_index: int | None      # 0, 1 или None при ничьей


def _indicates_problem(verdict: str | None) -> bool | None:
    if verdict is None:
        return None
    return verdict in ("dump", "litter")


def _speed_is_plausible(elapsed_ms: int | None) -> bool:
    return (
        elapsed_ms is not None
        and MIN_PLAUSIBLE_MS <= elapsed_ms <= MAX_REASONABLE_MS
    )


def score_duel(
    verdict_a: str | None,
    elapsed_a: int | None,
    verdict_b: str | None,
    elapsed_b: int | None,
) -> DuelOutcome:
    """Посчитать итог дуэли.

    Сравниваем на уровне «есть проблема / нет проблемы», а не точного
    типа: спутать скопление отходов с общей замусоренностью — простительно,
    решение инспектора от этого не меняется. Это та же мера, что и в
    контрольных заданиях, и одинаковость правил здесь не случайна.
    """
    problem_a = _indicates_problem(verdict_a)
    problem_b = _indicates_problem(verdict_b)

    agreed = (
        problem_a is not None and problem_b is not None and problem_a == problem_b
    )

    score_a = POINTS_PARTICIPATION if verdict_a is not None else 0
    score_b = POINTS_PARTICIPATION if verdict_b is not None else 0

    if agreed:
        score_a += POINTS_AGREEMENT
        score_b += POINTS_AGREEMENT

        # Бонус за скорость — только при согласии и только правдоподобному
        # времени. Иначе игра поощряла бы быстрые случайные клики.
        fast_a = _speed_is_plausible(elapsed_a)
        fast_b = _speed_is_plausible(elapsed_b)
        if fast_a and fast_b:
            if elapsed_a < elapsed_b:
                score_a += POINTS_SPEED
            elif elapsed_b < elapsed_a:
                score_b += POINTS_SPEED
        elif fast_a:
            score_a += POINTS_SPEED
        elif fast_b:
            score_b += POINTS_SPEED

    if score_a > score_b:
        winner = 0
    elif score_b > score_a:
        winner = 1
    else:
        winner = None

    return DuelOutcome(
        score_a=score_a, score_b=score_b, agreed=agreed, winner_index=winner
    )


# ------------------------------------------------------------------ #
# «Инспектор на день»
# ------------------------------------------------------------------ #

POINTS_INSPECTOR_CORRECT = 20
POINTS_INSPECTOR_WRONG = 5     # за попытку: игра обучающая, а не экзамен


def score_inspector_round(*, correct: bool) -> int:
    return POINTS_INSPECTOR_CORRECT if correct else POINTS_INSPECTOR_WRONG


def inspector_accuracy(correct: int, total: int) -> float | None:
    """Доля верных решений. None, пока раундов слишком мало — та же
    осторожность, что и с точностью разметки: показывать «0%» после
    одной ошибки означает потерять игрока."""
    MIN_ROUNDS = 5
    if total < MIN_ROUNDS:
        return None
    return correct / total
