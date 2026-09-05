"""Уведомления: e-mail и push.

Идея отдельного канала push через FCM пришла из предложения команды
фронтенда — она верная. Без него ключевая механика удержания не работает:
подписчик участка должен узнать о новом снимке, даже когда приложение
закрыто (KPI R1 — повторное участие).

Сейчас это заглушка с честным логированием. Реальная отправка появится
на этапе 1: FCM для push, SMTP или транзакционный провайдер для писем.
Интерфейс спроектирован так, чтобы подмена реализации не задела вызывающий
код: обработчики уже сегодня вызывают правильные методы.

Отправка всегда асинхронная и никогда не роняет основной запрос: если
уведомление не ушло, участок всё равно должен быть подтверждён.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger("kosmobereg.notifications")


class Channel(StrEnum):
    EMAIL = "email"
    PUSH = "push"


class NotificationType(StrEnum):
    """Типы уведомлений привязаны к этапам пользовательского пути."""

    NEW_SCENE_ON_SUBSCRIBED_SEGMENT = "new_scene"       # удержание, KPI R1
    REPORT_APPROVED = "report_approved"                 # подтверждение эффекта
    REPORT_REJECTED = "report_rejected"
    SEGMENT_APPROVED = "segment_approved"               # разметка пригодилась
    CONSENT_REQUESTED = "consent_requested"             # письмо родителю
    EVENT_REMINDER = "event_reminder"


@dataclass(frozen=True)
class Notification:
    type: NotificationType
    recipient: str
    # Ключ локализации и параметры, а не готовый текст: язык получателя
    # известен клиенту, а не отправителю. То же решение, что и в API.
    body_key: str
    vars: dict[str, str] | None = None
    channels: tuple[Channel, ...] = (Channel.PUSH,)


class NotificationService:
    """Точка входа для всех уведомлений системы."""

    async def send(self, notification: Notification) -> bool:
        """Отправить. Возвращает признак успеха, исключений не бросает."""
        try:
            for channel in notification.channels:
                logger.info(
                    "notification queued channel=%s type=%s to=%s key=%s vars=%s",
                    channel,
                    notification.type,
                    _mask(notification.recipient),
                    notification.body_key,
                    _safe_vars(notification.vars),
                )
            return True
        except Exception:  # pragma: no cover - защита основного сценария
            logger.exception("не удалось отправить уведомление")
            return False

    async def notify_subscribers_new_scene(
        self, recipients: list[str], segment_name_key: str
    ) -> None:
        for recipient in recipients:
            await self.send(
                Notification(
                    type=NotificationType.NEW_SCENE_ON_SUBSCRIBED_SEGMENT,
                    recipient=recipient,
                    body_key="notify.new_scene",
                    vars={"segment": segment_name_key},
                    channels=(Channel.PUSH,),
                )
            )

    async def notify_report_decision(
        self, recipient: str, *, approved: bool, segment_name_key: str
    ) -> None:
        await self.send(
            Notification(
                type=(
                    NotificationType.REPORT_APPROVED
                    if approved
                    else NotificationType.REPORT_REJECTED
                ),
                recipient=recipient,
                body_key=(
                    "notify.report_approved" if approved else "notify.report_rejected"
                ),
                vars={"segment": segment_name_key},
                channels=(Channel.PUSH, Channel.EMAIL),
            )
        )

    async def request_parent_consent(
        self, parent_contact: str, *, child_name: str, sign_url: str
    ) -> None:
        """Письмо законному представителю.

        Только e-mail: push сюда не годится, у родителя нет приложения,
        а согласие требует осознанного действия по ссылке.
        """
        await self.send(
            Notification(
                type=NotificationType.CONSENT_REQUESTED,
                recipient=parent_contact,
                body_key="notify.consent_requested",
                vars={"child": child_name, "url": sign_url},
                channels=(Channel.EMAIL,),
            )
        )


# Значения, которые нельзя писать в журнал даже частично: ссылка на
# подписание несёт одноразовый токен, а имя — персональные данные
# несовершеннолетнего.
SECRET_VARS = frozenset({"url", "token", "sign_url"})
PERSONAL_VARS = frozenset({"child", "name", "parent"})


def _safe_vars(vars_: dict[str, str] | None) -> dict[str, str]:
    """Убрать из журнала секреты и персональные данные.

    Без этого «честное логирование» само становилось каналом утечки:
    в лог попадали ФИО подростка и токен согласия, по которому можно
    подписать разрешение вместо родителя.
    """
    if not vars_:
        return {}
    safe: dict[str, str] = {}
    for key, value in vars_.items():
        if key in SECRET_VARS:
            safe[key] = "<скрыто>"
        elif key in PERSONAL_VARS:
            safe[key] = _mask(str(value))
        else:
            safe[key] = str(value)
    return safe


def _mask(contact: str) -> str:
    """Не пишем контакты в логи целиком — это персональные данные,
    в том числе данные третьих лиц (родителей)."""
    if "@" in contact:
        name, _, domain = contact.partition("@")
        head = name[:2] if len(name) > 2 else name[:1]
        return f"{head}***@{domain}"
    return contact[:3] + "***"


notifications = NotificationService()
