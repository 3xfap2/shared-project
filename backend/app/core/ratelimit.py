"""Ограничение частоты запросов для эндпоинтов авторизации.

Без него форма входа — это оракул для перебора паролей: злоумышленник
шлёт тысячи запросов в секунду, пока не угадает. Особенно опасно потому,
что часть наших пользователей — подростки, склонные к простым паролям.

РЕАЛИЗАЦИЯ НАМЕРЕННО ПРОСТАЯ. Счётчик в памяти процесса: работает сразу,
без Redis, и закрывает основной сценарий. Ограничение честно признаём:
при нескольких воркерах каждый считает свой лимит, поэтому фактический
порог умножается на их число.

В проде счётчик переезжает в Redis (он уже есть в архитектуре как брокер
очередей) — интерфейс при этом не меняется.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from app.core import errors
from app.core.config import settings


class SlidingWindowLimiter:
    """Скользящее окно: не более N запросов за period секунд с одного ключа."""

    def __init__(self, limit: int, period_seconds: int) -> None:
        self.limit = limit
        self.period = period_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = self._hits[key]

        while window and now - window[0] > self.period:
            window.popleft()

        if len(window) >= self.limit:
            retry_after = int(self.period - (now - window[0])) + 1
            raise errors.APIError(
                429,
                "too_many_requests",
                details={"retry_after_seconds": retry_after},
            )

        window.append(now)

        # Периодическая уборка: без неё словарь растёт на каждый новый IP
        # и превращается в утечку памяти.
        if len(self._hits) > 10_000:
            self._prune(now)

    def _prune(self, now: float) -> None:
        stale = [k for k, w in self._hits.items() if not w or now - w[-1] > self.period]
        for k in stale:
            del self._hits[k]

    def reset(self) -> None:
        """Для тестов: сбросить состояние между сценариями."""
        self._hits.clear()


def client_key(request: Request) -> str:
    """Ключ ограничения — адрес клиента.

    X-Forwarded-For принимается ТОЛЬКО от доверенного прокси из
    TRUSTED_PROXIES. Иначе клиент подставляет в заголовок произвольный
    адрес, счётчик заводится заново на каждый выдуманный, и ограничение
    обходится целиком.

    Прежняя версия доверяла заголовку безусловно, опираясь на то, что порт
    API не опубликован наружу. Это оказалось неверно: docker-compose его
    публикует, и вся защита обходилась одной строкой заголовка.
    """
    peer = request.client.host if request.client else None

    if peer is not None and peer in settings.TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Крайний левый адрес — исходный клиент, остальные дописал прокси.
            return forwarded.split(",")[0].strip()

    return peer or "unknown"


# Вход: перебор паролей. Пять попыток в минуту — человеку хватает,
# перебору нет.
login_limiter = SlidingWindowLimiter(limit=5, period_seconds=60)

# Регистрация: защита от массового создания аккаунтов ради накрутки
# консенсуса — прямая атака на контур доверия.
register_limiter = SlidingWindowLimiter(limit=10, period_seconds=3600)

# Подписание согласия: токен неугадываем, но перебор всё равно ограничиваем.
consent_limiter = SlidingWindowLimiter(limit=20, period_seconds=3600)
