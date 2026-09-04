"""Курс «Школа наблюдателя Земли» и присвоение допуска.

Структура курса живёт на сервере, тексты — в словаре фронта. Сервер знает
порядок модулей, число вариантов и правильные ответы; клиент знает, как
это показать на трёх языках.

ПРАВИЛЬНЫЕ ОТВЕТЫ НИКОГДА НЕ ПОКИДАЮТ СЕРВЕР. Иначе курс превращается в
формальность, а статус «Наблюдатель» — в кнопку «пропустить», хотя именно
он открывает доступ к данным реальной территории.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import Role
from app.models.user import User


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    order: int
    options_count: int
    correct_index: int      # только для сервера
    duration_min: int

    @property
    def title_key(self) -> str:
        return f"course.{self.id}.title"

    @property
    def theory_key(self) -> str:
        return f"course.{self.id}.theory"

    @property
    def question_key(self) -> str:
        return f"course.{self.id}.q"


# Порядок обязателен: модули проходятся последовательно.
MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(id="m1", order=1, options_count=3, correct_index=1, duration_min=6),
    ModuleSpec(id="m2", order=2, options_count=3, correct_index=1, duration_min=6),
    ModuleSpec(id="m3", order=3, options_count=3, correct_index=1, duration_min=7),
)

MODULES_BY_ID: dict[str, ModuleSpec] = {m.id: m for m in MODULES}
TOTAL_MODULES: int = len(MODULES)


def get_module(module_id: str) -> ModuleSpec | None:
    return MODULES_BY_ID.get(module_id)


def check_answer(module: ModuleSpec, answer_index: int) -> bool:
    return answer_index == module.correct_index


def is_course_complete(completed_module_ids: set[str]) -> bool:
    return all(m.id in completed_module_ids for m in MODULES)


def promote_on_completion(user: User) -> bool:
    """Повысить роль до «Наблюдателя» после завершения курса.

    Возвращает True, если роль изменилась.

    Повышаем только со `student`: у амбассадора и сотрудника ООПТ роль выше,
    и понижать её завершением курса было бы ошибкой.
    """
    if user.role != Role.STUDENT:
        return False
    user.role = Role.OBSERVER
    return True


def make_certificate_id(user_id: str, year: int) -> str:
    """Человекочитаемый номер сертификата.

    Формат КБ-<год>-<4 знака>: короткий, произносимый вслух и не
    раскрывающий число выданных сертификатов, в отличие от счётчика.
    """
    suffix = str(user_id).replace("-", "")[:4].upper()
    return f"КБ-{year}-{suffix}"
