"""Общие схемы ответов.

ЛОКАЛИЗАЦИЯ. API не возвращает переведённый текст — только доменные коды
и ключи словаря (`message_key`, `name_key`). Перевод выполняет фронт через
i18n.js. Решение зафиксировано в contract/README.md: словарь уже есть на
клиенте, дублировать его на сервере незачем, а четвёртый язык не должен
затрагивать бэкенд.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Schema(BaseModel):
    """Базовая схема: читает ORM-объекты напрямую."""

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(Schema):
    code: str = Field(description="Машинный код ошибки", examples=["consent_required"])
    message_key: str = Field(
        description="Ключ локализации для i18n.js",
        examples=["err.consent_required"],
    )
    details: dict[str, Any] | None = None


class Page(Schema, Generic[T]):
    items: list[T]
    total: int


class OkResponse(Schema):
    ok: bool = True
