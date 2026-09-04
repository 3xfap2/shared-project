"""Индекс внимания участка — ядро приоритизации.

    Индекс = 0.40·S + 0.30·C + 0.20·T + 0.10·A

Формула намеренно простая и объяснимая. Сотрудник ООПТ должен понимать,
почему участок оказался наверху списка: без этого не будет доверия, а без
доверия он не станет пользоваться системой (барьер B4 из паспорта).

ЕДИНСТВЕННОЕ МЕСТО ПЕРЕСЧЁТА. Присваивать `segment.attention_index`
где-либо ещё запрещено. Рассинхрон факторов и индекса означает, что
инспектор поедет не туда.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings
from app.db.base import utcnow
from app.models.geo import Segment

# Через сколько дней без проверки фактор давности достигает максимума.
# Год — осознанный выбор: береговая линия проходит полный сезонный цикл,
# и участок, не проверявшийся год, требует внимания независимо от истории.
RECENCY_SATURATION_DAYS = 365.0


@dataclass(frozen=True)
class Factors:
    """Факторы индекса. Каждый нормирован в [0, 1]."""

    s: float  # сигнал ДЗЗ: величина изменения между снимками
    c: float  # консенсус людей: взвешенная доля отметивших проблему
    t: float  # давность последней проверки
    a: float  # доступность участка для волонтёров

    def __post_init__(self) -> None:
        for name in ("s", "c", "t", "a"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Фактор {name} вне диапазона [0,1]: {value}")


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_index(factors: Factors) -> int:
    """Взвешенная сумма факторов, приведённая к шкале 0..100.

    Возвращает целое: дробные доли балла не несут смысла для инспектора,
    а целое число проще сравнивать глазами в списке приоритетов.
    """
    w = settings.attention_weights
    raw = (
        w["s"] * factors.s
        + w["c"] * factors.c
        + w["t"] * factors.t
        + w["a"] * factors.a
    )
    return int(round(clamp01(raw) * 100))


def compute_recency(last_verified_at: datetime | None, *, now: datetime | None = None,
                    fallback: datetime | None = None) -> float:
    """Фактор T: сколько времени участок не проверяли.

    Никогда не проверявшийся участок отсчитывается от момента появления
    в системе, а не считается сразу максимально запущенным — иначе все
    новые участки разом заняли бы верх списка.
    """
    now = now or utcnow()
    reference = last_verified_at or fallback
    if reference is None:
        # Ни проверки, ни даты появления — считаем нейтрально.
        return 0.5

    # Защита от наивных datetime, попавших из внешних источников.
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=now.tzinfo)

    days = (now - reference).total_seconds() / 86400.0
    if days <= 0:
        return 0.0
    return clamp01(days / RECENCY_SATURATION_DAYS)


def recalculate(segment: Segment, *, now: datetime | None = None) -> int:
    """Пересчитать фактор давности и индекс участка. Возвращает новый индекс.

    Факторы S, C и A изменяются своими источниками (конвейер ДЗЗ, сервис
    консенсуса, настройка территории), а T зависит только от времени,
    поэтому пересчитывается здесь при каждом обращении.
    """
    now = now or utcnow()
    segment.factor_t = compute_recency(
        segment.last_verified_at, now=now, fallback=segment.created_at
    )
    factors = Factors(
        s=clamp01(segment.factor_s),
        c=clamp01(segment.factor_c),
        t=clamp01(segment.factor_t),
        a=clamp01(segment.factor_a),
    )
    segment.attention_index = compute_index(factors)
    return segment.attention_index


def apply_cleanup(segment: Segment, *, now: datetime | None = None) -> int:
    """Последствия подтверждённой уборки — момент замыкания цикла.

    Что происходит и почему:
      * T обнуляется — участок только что проверен;
      * C снижается — проблема, о которой сообщали разметчики, устранена;
      * S снижается — на следующем снимке аномалии ожидаемо не будет.

    S уменьшается, а не обнуляется: мы не видели новый снимок и не вправе
    утверждать, что сигнал исчез. Окончательно его поправит конвейер ДЗЗ,
    когда придёт свежая сцена.
    """
    now = now or utcnow()
    segment.last_verified_at = now
    segment.factor_c = clamp01(segment.factor_c - 0.35)
    segment.factor_s = clamp01(segment.factor_s - 0.30)
    return recalculate(segment, now=now)
