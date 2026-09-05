"""Реестр моделей.

Импорт всех моделей в одном месте обязателен: Alembic и `Base.metadata`
видят только те таблицы, чьи модули были импортированы. Без этого файла
автогенерация миграций молча пропустит половину схемы.
"""

from app.models.annotation import Annotation
from app.models.audit import AuditLog
from app.models.enums import (
    AuditAction,
    ConsentStatus,
    DuelStatus,
    ImageryPriority,
    ImageryRequestStatus,
    EnrollmentStatus,
    EventStatus,
    ModerationDecision,
    ReportStatus,
    Requirement,
    Role,
    SceneSource,
    SegmentStatus,
    Verdict,
)
from app.models.event import Consent, Enrollment, Event
from app.models.game import Duel, DuelEntry, InspectorRound
from app.models.imagery import ImageryRequest
from app.models.geo import Oopt, Scene, Segment
from app.models.learning import CourseProgress
from app.models.report import FieldReport, Media
from app.models.user import Subscription, User

__all__ = [
    # Таблицы
    "Annotation",
    "AuditLog",
    "Consent",
    "CourseProgress",
    "Duel",
    "DuelEntry",
    "Enrollment",
    "Event",
    "FieldReport",
    "ImageryRequest",
    "InspectorRound",
    "Media",
    "Oopt",
    "Scene",
    "Segment",
    "Subscription",
    "User",
    # Перечисления
    "AuditAction",
    "ConsentStatus",
    "DuelStatus",
    "EnrollmentStatus",
    "EventStatus",
    "ImageryPriority",
    "ImageryRequestStatus",
    "ModerationDecision",
    "ReportStatus",
    "Requirement",
    "Role",
    "SceneSource",
    "SegmentStatus",
    "Verdict",
]
