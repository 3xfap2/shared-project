"""Доменные перечисления.

Значения совпадают с контрактом contract/openapi.yaml. Менять их можно
только вместе с контрактом — иначе фронт и бэк разойдутся.

Хранятся как VARCHAR с CHECK-ограничением (native_enum=False), поэтому
одинаково работают на PostgreSQL и SQLite, а добавление нового значения —
обычная миграция, а не ALTER TYPE.
"""

from enum import StrEnum


class Role(StrEnum):
    """Роли. Порядок важен: используется для проверки «не ниже чем»."""

    STUDENT = "student"          # зарегистрирован, курс не завершён
    OBSERVER = "observer"        # курс завершён — допуск к реальной разметке
    AMBASSADOR = "ambassador"    # ядро сообщества, подаёт заявки на акции
    OOPT_STAFF = "oopt_staff"    # сотрудник ООПТ, только своя территория
    ADMIN = "admin"              # команда проекта


# Иерархия прав волонтёрской ветки. oopt_staff и admin проверяются отдельно,
# потому что это не «более высокий волонтёр», а другая ветвь доступа.
VOLUNTEER_HIERARCHY: dict[Role, int] = {
    Role.STUDENT: 0,
    Role.OBSERVER: 1,
    Role.AMBASSADOR: 2,
}


class SegmentStatus(StrEnum):
    PROBLEM = "problem"   # выявлена проблема
    WORK = "work"         # подтверждён инспектором, в работе
    CLEAN = "clean"       # уборка подтверждена
    WATCH = "watch"       # под наблюдением


class Verdict(StrEnum):
    DUMP = "dump"       # скопление отходов
    LITTER = "litter"   # общая замусоренность
    NONE = "none"       # проблемы нет


class SceneSource(StrEnum):
    RESURS_P = "resurs-p"
    KANOPUS_V = "kanopus-v"
    SENTINEL_2 = "sentinel-2"
    UAV = "uav"          # аэрофотосъёмка с БПЛА


class EventStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class EnrollmentStatus(StrEnum):
    PENDING_REQUIREMENTS = "pending_requirements"  # допуск не оформлен
    READY = "ready"                                # допущен
    ATTENDED = "attended"                          # был на акции
    CANCELLED = "cancelled"


class Requirement(StrEnum):
    """Что мешает допуску. Фронт ведёт пользователя по этому списку."""

    COURSE_COMPLETION = "course_completion"
    PARENT_CONSENT = "parent_consent"
    BRIEFING = "briefing"


class ConsentStatus(StrEnum):
    NOT_REQUIRED = "not_required"   # совершеннолетний
    REQUESTED = "requested"         # ссылка отправлена
    SIGNED = "signed"               # согласие получено


class ReportStatus(StrEnum):
    DRAFT = "draft"         # есть фото «до», уборка идёт
    PENDING = "pending"     # отправлен на модерацию
    APPROVED = "approved"
    REJECTED = "rejected"


class ModerationDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ImageryRequestStatus(StrEnum):
    """Жизненный цикл заявки на новую съёмку.

    Отражает реальный процесс оператора ДЗЗ: заявка попадает в очередь,
    принимается к исполнению, снимок доставляется. Отказ возможен на
    любом шаге — облачность, приоритет других задач, ограничения
    по территории.
    """

    QUEUED = "queued"        # отправлена оператору
    ACCEPTED = "accepted"    # принята к съёмке
    DELIVERED = "delivered"  # снимок получен и нарезан
    REJECTED = "rejected"


class ImageryPriority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"        # растущая свалка, сигнал надзорных органов


class AuditAction(StrEnum):
    """Действия, попадающие в неизменяемый журнал.

    Журнал нужен ООПТ для отчётности: любое решение, повлиявшее на данные
    территории, должно быть прослеживаемым.
    """

    SEGMENT_APPROVED = "segment_approved"
    SEGMENT_REJECTED = "segment_rejected"
    REPORT_APPROVED = "report_approved"
    REPORT_REJECTED = "report_rejected"
    EVENT_CREATED = "event_created"
    ROLE_CHANGED = "role_changed"
    CONSENT_SIGNED = "consent_signed"
    IMAGERY_REQUESTED = "imagery_requested"
