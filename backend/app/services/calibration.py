"""Скрытые эталонные задания — измерение реальной точности разметчиков.

ЗАЧЕМ. Репутация, выведенная только из решений модератора, отражает не
точность человека, а то, насколько повезло с участками. Нам нужна прямая
мера: волонтёру незаметно подмешиваются участки, по которым инспектор уже
вынес решение, и мы сравниваем его ответ с эталоном.

Это закрывает главный барьер второй аудитории. Инспектор видит не «трое
подростков что-то отметили», а «трое с подтверждённой точностью 87%» —
разница между надеждой и данными.

ГЛАВНОЕ ПРАВИЛО: признак контрольного задания не покидает сервер.
Схема задания намеренно не содержит такого поля. Как только волонтёр
узнаёт, что его проверяют, он начинает стараться иначе — и измерение
перестаёт отражать его обычную работу.

Приём заимствован у Zooniverse (gold standard tasks), где на миллионах
разметок доказал, что неспециалисты дают пригодные данные при правильной
проверке.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.core.config import settings
from app.models.enums import Verdict
from app.models.geo import Segment
from app.models.user import User


@dataclass(frozen=True)
class CalibrationOutcome:
    was_control: bool
    was_correct: bool | None


def should_serve_control(rng: random.Random | None = None) -> bool:
    """Пора ли подсунуть контрольное задание.

    Случайно, а не по расписанию: предсказуемая частота («каждое седьмое»)
    вычисляется пользователями за день и обесценивает проверку.
    """
    r = rng or random
    return r.random() < settings.CONTROL_TASK_PROBABILITY


def truth_from_decision(*, approved: bool, prevailing: Verdict) -> Verdict:
    """Эталонный ответ по участку после решения инспектора.

    Подтвердил — эталоном становится преобладающий вердикт разметчиков.
    Отклонил — эталон «проблемы нет»: инспектор проверил и не нашёл её.
    """
    return prevailing if approved else Verdict.NONE


def check_answer(segment: Segment, verdict: Verdict) -> bool:
    """Совпал ли ответ волонтёра с эталоном.

    Сравниваем на уровне «есть проблема / нет проблемы», а не точного типа:
    спутать скопление отходов с общей замусоренностью — простительно и не
    меняет решения инспектора. Пропустить проблему или выдумать её —
    ошибка по существу.
    """
    if segment.control_truth is None:
        return False

    truth_has_problem = segment.control_truth != Verdict.NONE
    answer_has_problem = verdict != Verdict.NONE
    return truth_has_problem == answer_has_problem


def record_result(user: User, *, correct: bool) -> None:
    """Учесть результат контрольного задания в профиле разметчика."""
    user.control_tasks_count += 1
    if correct:
        user.control_correct_count += 1


def blend_reputation(user: User) -> float:
    """Свести репутацию и измеренную точность в один вес разметки.

    Пока контрольных заданий мало, доверяем репутации от модератора.
    Когда накопилась статистика, точность становится основным сигналом:
    она измерена напрямую, а не выведена косвенно.

    Возвращает вес в тех же границах, что и репутация, — чтобы формула
    консенсуса не изменилась.
    """
    accuracy = user.accuracy
    if accuracy is None:
        return user.reputation

    # Доля измеренного растёт с числом заданий и упирается в 0.7:
    # полностью отказываться от истории модерации не стоит, она ловит то,
    # чего контрольные задания не видят.
    confidence = min(0.7, user.control_tasks_count / 40.0)
    blended = user.reputation * (1 - confidence) + (accuracy * 1.5) * confidence
    return max(settings.REPUTATION_MIN, min(settings.REPUTATION_MAX, blended))
