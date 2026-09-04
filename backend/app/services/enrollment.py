"""Допуск волонтёра на акцию.

САМАЯ ОТВЕТСТВЕННАЯ ЛОГИКА В ПРОЕКТЕ. Здесь решается, попадёт ли
несовершеннолетний на охраняемую территорию. Ошибка тут — не баг в
интерфейсе, а реальный человек в поле без согласия родителя.

Правила проектирования этого модуля:
  * условия допуска вычисляются из фактического состояния, а не читаются
    из флага, который кто-то мог выставить;
  * проверка выполняется на сервере при каждом обращении, а не один раз
    при записи: роль или согласие могли измениться;
  * список невыполненных условий возвращается фронту целиком — интерфейс
    ведёт пользователя по нему, но не решает сам, что можно пропустить.
"""

from __future__ import annotations

from app.models.enums import EnrollmentStatus, Requirement, Role
from app.models.event import Enrollment
from app.models.user import User


def evaluate_requirements(user: User, enrollment: Enrollment) -> list[Requirement]:
    """Что осталось выполнить для допуска. Пустой список = допущен."""
    blocking: list[Requirement] = []

    # 1. Курс. Без него нет ни понимания техники безопасности, ни смысла
    #    выхода — на акцию едут проверять собственную разметку.
    if user.role == Role.STUDENT:
        blocking.append(Requirement.COURSE_COMPLETION)

    # 2. Согласие родителя — только для несовершеннолетних.
    #    Проверяем именно подписанное согласие, а не факт отправки запроса.
    if user.is_minor:
        consent = enrollment.consent
        if consent is None or not consent.is_signed:
            blocking.append(Requirement.PARENT_CONSENT)

    # 3. Инструктаж — последним: он про то, как вести себя на конкретной
    #    территории, и имеет смысл только когда остальное готово.
    if enrollment.briefing_completed_at is None:
        blocking.append(Requirement.BRIEFING)

    return blocking


def is_admitted(user: User, enrollment: Enrollment) -> bool:
    return not evaluate_requirements(user, enrollment)


def sync_status(user: User, enrollment: Enrollment) -> EnrollmentStatus:
    """Привести денормализованный статус записи в соответствие с фактами.

    Вызывается после любого изменения условий. Статусы `attended`
    и `cancelled` — терминальные, их пересчёт не трогает.
    """
    if enrollment.status in (EnrollmentStatus.ATTENDED, EnrollmentStatus.CANCELLED):
        return enrollment.status

    enrollment.status = (
        EnrollmentStatus.READY
        if is_admitted(user, enrollment)
        else EnrollmentStatus.PENDING_REQUIREMENTS
    )
    return enrollment.status
