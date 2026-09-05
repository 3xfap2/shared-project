"""Регистрация, вход, профиль."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import errors
from app.core.config import settings
from app.core.deps import CurrentUser, SessionDep
from app.core.ratelimit import client_key, login_limiter, register_limiter
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.base import utcnow
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeOut,
    RefreshRequest,
    RegisterRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(user: User) -> AuthResponse:
    return AuthResponse(
        access_token=create_access_token(str(user.id), user.role.value),
        refresh_token=create_refresh_token(str(user.id)),
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация",
)
async def register(
    request: Request, payload: RegisterRequest, session: SessionDep
) -> AuthResponse:
    """Создать аккаунт.

    Возраст приходит числом, а хранится годом рождения — иначе значение
    устареет и система будет требовать согласие родителя у совершеннолетнего.

    Частота ограничена: массовая регистрация — прямая атака на контур
    доверия, накрутка консенсуса пачкой аккаунтов.
    """
    register_limiter.check(client_key(request))
    email = payload.email.lower().strip()

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        name=payload.name,
        birth_year=date.today().year - payload.age,
        city=payload.city,
    )
    session.add(user)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Уникальность проверяет база, а не предварительный SELECT:
        # между проверкой и вставкой возможна гонка.
        raise errors.conflict("email_taken") from exc

    await session.refresh(user)
    return _issue(user)


@router.post("/login", response_model=AuthResponse, summary="Вход")
async def login(
    request: Request, payload: LoginRequest, session: SessionDep
) -> AuthResponse:
    """Вход. Частота ограничена — иначе форма входа работает как оракул
    для перебора паролей."""
    login_limiter.check(client_key(request))
    email = payload.email.lower().strip()
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Один и тот же ответ на «нет такого пользователя» и «неверный пароль»:
    # иначе форма входа превращается в способ узнать, кто зарегистрирован.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise errors.unauthorized("invalid_credentials")
    if not user.is_active:
        raise errors.forbidden("account_disabled")

    user.last_login_at = utcnow()
    await session.commit()
    await session.refresh(user)
    return _issue(user)


@router.post("/refresh", response_model=AuthResponse, summary="Обновить токены")
async def refresh(payload: RefreshRequest, session: SessionDep) -> AuthResponse:
    import uuid as _uuid

    try:
        data = decode_token(payload.refresh_token, "refresh")
    except TokenError as exc:
        raise errors.unauthorized(str(exc)) from exc

    try:
        user_id = _uuid.UUID(data["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise errors.unauthorized("token_invalid") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise errors.unauthorized("user_not_found")

    return _issue(user)


@router.get("/me", response_model=MeOut, summary="Текущий пользователь")
async def me(user: CurrentUser) -> MeOut:
    return MeOut.model_validate(user)
