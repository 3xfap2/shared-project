"""Консенсус разметки и репутация разметчиков.

Это контур доверия — то, ради чего сотрудник ООПТ вообще согласится
смотреть на данные от подростков. Логика намеренно простая: инспектор
должен понимать, откуда взялось число, а не верить чёрному ящику.

Как это работает:
  1. Волонтёр отправляет разметку. Её вес — репутация автора на момент
     отправки (фиксируется в самой разметке, не берётся задним числом).
  2. Фактор C участка = взвешенная доля тех, кто увидел проблему.
  3. Когда набралось N независимых подтверждений проблемы, участок уходит
     в очередь модерации ООПТ.
  4. Решение инспектора меняет репутацию разметчиков: подтвердил — вверх,
     отклонил — вниз. Так система сама вычищает тех, кто размечает наугад
     ради баллов (риск R-P4 из паспорта).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.core.config import settings
from app.models.annotation import Annotation
from app.models.enums import Verdict


@dataclass(frozen=True)
class ConsensusResult:
    votes: int              # подтверждений проблемы
    total: int              # всего разметок по участку
    votes_required: int
    factor_c: float         # взвешенная доля увидевших проблему
    reached: bool           # достигнут ли порог

    @property
    def agreement(self) -> float:
        """Доля согласия — показывается инспектору при модерации."""
        return self.votes / self.total if self.total else 0.0


def indicates_problem(verdict: Verdict) -> bool:
    return verdict in (Verdict.DUMP, Verdict.LITTER)


def evaluate(annotations: Iterable[Annotation]) -> ConsensusResult:
    """Посчитать консенсус по всем разметкам участка.

    Фактор C — взвешенная по репутации доля, а не простой процент:
    разметка человека с подтверждённой историей весит больше, чем разметка
    новичка. Это защищает от накрутки массовыми аккаунтами.
    """
    items = list(annotations)
    required = settings.CONSENSUS_VOTES_REQUIRED

    if not items:
        return ConsensusResult(0, 0, required, 0.0, False)

    total_weight = sum(max(a.weight, 0.0) for a in items)
    problem_weight = sum(
        max(a.weight, 0.0) for a in items if indicates_problem(a.verdict)
    )
    votes = sum(1 for a in items if indicates_problem(a.verdict))

    factor_c = (problem_weight / total_weight) if total_weight > 0 else 0.0

    return ConsensusResult(
        votes=votes,
        total=len(items),
        votes_required=required,
        factor_c=min(1.0, max(0.0, factor_c)),
        reached=votes >= required,
    )


def adjust_reputation(current: float, *, approved: bool) -> float:
    """Новое значение репутации после решения модератора.

    Штраф вдвое больше награды (-0.20 против +0.10): случайная разметка
    должна становиться невыгодной быстрее, чем добросовестная — выгодной.
    Границы не дают ни обнулить репутацию навсегда, ни накопить
    неограниченное влияние.
    """
    delta = (
        settings.REPUTATION_ON_APPROVE if approved else settings.REPUTATION_ON_REJECT
    )
    return max(settings.REPUTATION_MIN, min(settings.REPUTATION_MAX, current + delta))
