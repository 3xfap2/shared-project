"""Конфигурация приложения.

Все настройки читаются из переменных окружения. Значения по умолчанию
пригодны только для локальной разработки — в проде обязательны SECRET_KEY
и DATABASE_URL из окружения.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Общее ---
    ENV: Literal["local", "test", "staging", "production"] = "local"
    APP_NAME: str = "KOSMOBEREG API"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # --- База данных ---
    # Прод:   postgresql+asyncpg://user:pass@host:5432/kosmobereg
    # Локально/тесты: sqlite+aiosqlite:///./kosmobereg.db
    DATABASE_URL: str = "sqlite+aiosqlite:///./kosmobereg.db"
    DB_ECHO: bool = False

    # --- Безопасность ---
    SECRET_KEY: str = "dev-only-insecure-key-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 60
    REFRESH_TOKEN_TTL_DAYS: int = 30

    # --- CORS ---
    # Фронтенд работает на отдельном порту, поэтому явный список источников.
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ]

    # --- Доменные правила ---
    # Порог консенсуса: сколько независимых разметок нужно, чтобы участок
    # попал в очередь модерации ООПТ. Вынесен в конфиг, потому что при росте
    # шума порог поднимают (риск R-P3 из паспорта).
    CONSENSUS_VOTES_REQUIRED: int = 3

    # Минимальный возраст регистрации. Кейс задаёт аудиторию 14+.
    MIN_REGISTRATION_AGE: int = 14
    # Возраст совершеннолетия: ниже — особый режим работы с ПДн.
    ADULT_AGE: int = 18

    # Веса индекса внимания: 0.40·S + 0.30·C + 0.20·T + 0.10·A
    # Калибруются на пилоте по доле подтверждений инспектором (KPI Q1).
    ATTENTION_WEIGHT_S: float = 0.40
    ATTENTION_WEIGHT_C: float = 0.30
    ATTENTION_WEIGHT_T: float = 0.20
    ATTENTION_WEIGHT_A: float = 0.10

    # Попыток на модуль курса. Модулей три, вариантов ответа три — без
    # лимита курс проходится перебором за шесть запросов, а он служит
    # условием допуска на охраняемую территорию.
    MAX_MODULE_ATTEMPTS: int = 5

    # Адреса, которым можно верить в заголовке X-Forwarded-For. Пустой
    # список означает, что заголовок игнорируется полностью. В проде сюда
    # попадает адрес обратного прокси.
    TRUSTED_PROXIES: list[str] = []

    # --- Калибровка разметчиков (скрытые эталонные задания) ---
    # Доля контрольных заданий в выдаче. 15% — компромисс: достаточно для
    # статистики за разумное время и не настолько много, чтобы волонтёр
    # тратил заметную часть усилий на уже решённые участки.
    CONTROL_TASK_PROBABILITY: float = 0.15
    # Ниже этого числа заданий точность не показываем: «0% после одной
    # ошибки» — способ потерять новичка.
    MIN_CONTROL_TASKS_FOR_ACCURACY: int = 5

    # --- Детектор растущей свалки ---
    # Прирост площади аномалии, начиная с которого участок считается
    # растущим. 15% отсекает шум измерения между снимками.
    GROWTH_ALERT_THRESHOLD: float = 0.15
    # Насколько рост усиливает спутниковый сигнал S. Формула индекса при
    # этом не меняется — усиление происходит внутри конвейера ДЗЗ.
    GROWTH_SIGNAL_BOOST: float = 0.20

    # Изменение репутации разметчика по решению модератора.
    REPUTATION_ON_APPROVE: float = 0.10
    REPUTATION_ON_REJECT: float = -0.20
    REPUTATION_MIN: float = 0.20
    REPUTATION_MAX: float = 1.50

    # --- Загрузка файлов ---
    MAX_UPLOAD_BYTES: int = 15 * 1024 * 1024  # 15 МБ
    ALLOWED_IMAGE_TYPES: list[str] = ["image/jpeg", "image/png", "image/webp"]
    MEDIA_ROOT: str = "./media"

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_must_be_set_in_prod(cls, v: str, info) -> str:
        """Не пустить в прод ключ-заглушку.

        Проверяем не одну конкретную строку, а признак «это явно не
        секрет»: раньше валидатор ловил только `dev-only-...`, а
        `.env.example` предлагал `change-me-in-production` — и защита
        обходилась ровно тем значением, которое проект сам и подсказывал.
        """
        env = (info.data or {}).get("ENV")
        if env not in ("staging", "production"):
            return v

        lowered = v.lower()
        looks_placeholder = any(
            marker in lowered
            for marker in ("change", "dev-only", "insecure", "example", "secret-key")
        )
        if looks_placeholder or len(v) < 32:
            raise ValueError(
                "SECRET_KEY в staging/production должен быть случайной строкой "
                "не короче 32 символов, а не значением-заглушкой"
            )
        return v

    @property
    def attention_weights(self) -> dict[str, float]:
        return {
            "s": self.ATTENTION_WEIGHT_S,
            "c": self.ATTENTION_WEIGHT_C,
            "t": self.ATTENTION_WEIGHT_T,
            "a": self.ATTENTION_WEIGHT_A,
        }

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
