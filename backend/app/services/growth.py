"""Детектор растущей аномалии.

ПОЧЕМУ ЭТО ВАЖНО. Абсолютный размер свалки — плохой признак срочности.
Большая, но стабильная свалка стоит там годами и подождёт ещё месяц.
Маленькая, но растущая — это активный источник, и через сезон она станет
большой. Ловить нужно вторую.

Идея не выдумана: на данных КА «Ресурс-П» был зафиксирован именно рост
несанкционированной свалки вблизи заповедника за 2017–2019 годы. Мы
переносим этот приём в продукт.

ФОРМУЛА ИНДЕКСА ПРИ ЭТОМ НЕ МЕНЯЕТСЯ. Рост усиливает спутниковый сигнал S
внутри конвейера ДЗЗ, а не добавляет пятое слагаемое. Так паспорт,
презентация и прототип остаются верными, а поведение системы улучшается.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.config import settings
from app.models.geo import Scene, Segment


@dataclass(frozen=True)
class GrowthResult:
    rate: float | None          # доля прироста площади между двумя последними сценами
    is_growing: bool
    area_from: float | None
    area_to: float | None
    boosted_signal: float       # фактор S после усиления


def compute_growth(scenes: Sequence[Scene]) -> tuple[float | None, float | None, float | None]:
    """Прирост площади аномалии между двумя последними измеренными сценами.

    Возвращает (доля прироста, площадь «до», площадь «после»).
    None, если измерений меньше двух — судить о динамике не по чему.

    Берём именно две последние сцены с измеренной площадью, а не первую
    и последнюю: нас интересует текущая динамика, а не изменение за всё
    время наблюдения. Свалка, выросшая год назад и с тех пор стабильная,
    срочности не требует.
    """
    measured = [s for s in scenes if s.anomaly_area_m2 is not None]
    if len(measured) < 2:
        return None, None, None

    measured.sort(key=lambda s: s.captured_at)
    previous, latest = measured[-2], measured[-1]

    area_from = float(previous.anomaly_area_m2 or 0.0)
    area_to = float(latest.anomaly_area_m2 or 0.0)

    if area_from <= 0:
        # Аномалия появилась там, где её не было. Это максимальная срочность,
        # но делить на ноль нельзя — возвращаем полный прирост.
        return (1.0 if area_to > 0 else 0.0), area_from, area_to

    return (area_to - area_from) / area_from, area_from, area_to


def apply_growth(segment: Segment, scenes: Sequence[Scene]) -> GrowthResult:
    """Пересчитать динамику участка и усилить сигнал S при росте.

    Усиление пропорционально скорости роста, но ограничено сверху: даже
    взрывной рост не должен один вытеснить все остальные факторы —
    спутник по-прежнему даёт гипотезу, а не диагноз.
    """
    rate, area_from, area_to = compute_growth(scenes)
    segment.growth_rate = rate

    base_signal = max(0.0, min(1.0, segment.factor_s))
    boosted = base_signal

    if rate is not None and rate >= settings.GROWTH_ALERT_THRESHOLD:
        # Полное усиление достигается при удвоении площади (rate = 1.0).
        share = min(1.0, rate)
        boosted = min(1.0, base_signal + settings.GROWTH_SIGNAL_BOOST * share)
        segment.factor_s = boosted

    return GrowthResult(
        rate=rate,
        is_growing=segment.is_growing,
        area_from=area_from,
        area_to=area_to,
        boosted_signal=boosted,
    )
