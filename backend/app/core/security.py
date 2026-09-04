"""Хеширование паролей и работа с JWT.

Пароли: SHA-256 → bcrypt.
    bcrypt молча обрезает вход на 72 байтах. Для кириллицы это ~36 символов,
    и два разных длинных пароля могли бы совпасть. Поэтому пароль сначала
    сворачивается в SHA-256 (64 ASCII-символа), а уже он подаётся в bcrypt.
    Ограничение снимается полностью, стойкость bcrypt сохраняется.

Токены: access + refresh.
    Access — короткоживущий, несёт роль для быстрой проверки прав.
    Refresh — долгоживущий, обменивается на новую пару.
    Роль в токене нужна только для отсечения заведомо чужих запросов;
    любое действие, меняющее данные, перепроверяет роль по базе.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings

TokenType = Literal["access", "refresh"]


# --------------------------------------------------------------------------
# Пароли
# --------------------------------------------------------------------------

def _prehash(password: str) -> bytes:
    """SHA-256 от пароля в виде hex — снимает лимит bcrypt в 72 байта."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # Повреждённый или отсутствующий хеш не должен ронять запрос.
        return False


# --------------------------------------------------------------------------
# Токены
# --------------------------------------------------------------------------

def _create_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, role: str) -> str:
    return _create_token(
        subject=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
        extra={"role": role},
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        subject=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
    )


class TokenError(Exception):
    """Токен отсутствует, истёк, повреждён или имеет неверный тип."""


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("token_invalid") from exc

    if payload.get("type") != expected_type:
        # Refresh-токен не должен приниматься там, где ждут access, и наоборот.
        raise TokenError("token_wrong_type")
    if not payload.get("sub"):
        raise TokenError("token_invalid")

    return payload
