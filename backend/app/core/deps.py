"""Зависимости FastAPI: сессия, текущий пользователь, проверка ролей.

ПРИНЦИП. Роль из JWT используется только чтобы быстро отсечь заведомо
чужой запрос. Любое действие, меняющее данные, перепроверяет роль по базе:
токен живёт час, а роль могла измениться — например, аккаунт заблокировали.
Поэтому `get_current_user` всегда ходит в базу, а не верит полю в токене.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.security import TokenError, decode_token
from app.db.session import get_session
from app.models.enums import Role
from app.models.user import User

# auto_error=False: отсутствующий заголовок должен превращаться в нашу
# ошибку с кодом и ключом перевода, а не в стандартный ответ FastAPI.
bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise errors.unauthorized("missing_token")

    try:
        payload = decode_token(credentials.credentials, "access")
    except TokenError as exc:
        raise errors.unauthorized(str(exc)) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise errors.unauthorized("token_invalid") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise errors.unauthorized("user_not_found")
    if not user.is_active:
        raise errors.forbidden("account_disabled")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User | None:
    """Для публичных эндпоинтов, которым полезно знать пользователя,
    но которые работают и без него (карта, лендинг)."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(session, credentials)
    except Exception:
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require_roles(*allowed: Role) -> Callable[[User], User]:
    """Пропустить только перечисленные роли. Администратор проходит всегда."""

    async def _guard(user: CurrentUser) -> User:
        if user.role == Role.ADMIN or user.role in allowed:
            return user
        raise errors.forbidden(
            "role_not_allowed",
            required=[r.value for r in allowed],
            actual=user.role.value,
        )

    return _guard


async def require_observer(user: CurrentUser) -> User:
    """Допуск к реальной разметке — только после завершения курса.

    Отдельная ошибка `course_not_completed`, а не общий `forbidden`:
    фронт по этому коду отправляет пользователя доучиваться, а не показывает
    «доступ запрещён». Это часть пользовательского пути, а не тупик.
    """
    if not user.can_annotate():
        raise errors.forbidden("course_not_completed")
    return user


async def require_oopt_staff(user: CurrentUser) -> User:
    if not user.is_oopt_staff:
        raise errors.forbidden("role_not_allowed", required=["oopt_staff"])
    if user.role != Role.ADMIN and user.oopt_id is None:
        # Сотрудник без территории не должен видеть чужие данные.
        raise errors.forbidden("no_territory_assigned")
    return user


ObserverUser = Annotated[User, Depends(require_observer)]
StaffUser = Annotated[User, Depends(require_oopt_staff)]
