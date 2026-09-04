"""Схемы обучающего контура."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.enums import Role
from app.schemas.common import Schema


class CourseModuleOut(Schema):
    """Структура модуля. Тексты живут в словаре фронта, здесь только ключи.

    Правильный ответ не передаётся никогда — иначе курс превращается
    в формальность, а он открывает доступ к данным реальной территории.
    """

    id: str
    order: int
    title_key: str
    theory_key: str
    question_key: str
    options_count: int
    duration_min: int
    completed: bool = False


class CourseProgressOut(Schema):
    completed_modules: list[str]
    total_modules: int
    course_completed: bool
    started_at: datetime | None = None
    completed_at: datetime | None = None
    certificate_id: str | None = None


class AnswerRequest(Schema):
    answer_index: int = Field(ge=0, le=9)


class AnswerResult(Schema):
    correct: bool
    module_completed: bool
    course_completed: bool = False
    role_changed_to: Role | None = Field(
        default=None,
        description="Заполняется только в момент завершения курса",
    )
    certificate_id: str | None = None
