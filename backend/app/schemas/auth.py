"""Схемы регистрации, входа и профиля."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import EmailStr, Field, field_validator

from app.core.config import settings
from app.models.enums import Role
from app.schemas.common import Schema


class RegisterRequest(Schema):
    name: str = Field(min_length=1, max_length=100, examples=["Аня"])
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    age: int = Field(
        ge=settings.MIN_REGISTRATION_AGE,
        le=120,
        examples=[16],
        description=(
            "Возраст при регистрации. Сервер хранит год рождения, чтобы "
            "значение не устаревало."
        ),
    )
    city: str | None = Field(default=None, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name_empty")
        return v

    @field_validator("password")
    @classmethod
    def _password_not_trivial(cls, v: str) -> str:
        if v.isdigit():
            raise ValueError("password_all_digits")
        return v


class LoginRequest(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(Schema):
    """Профиль пользователя.

    E-mail намеренно не возвращается в публичных представлениях —
    только владельцу через /auth/me.
    """

    id: uuid.UUID
    name: str
    role: Role
    is_minor: bool
    reputation: float
    volunteer_hours: float
    annotations_count: int
    certificate_id: str | None = None
    oopt_id: str | None = None
    city: str | None = None

    # Измеренная точность разметки по скрытым контрольным заданиям.
    # None, пока заданий меньше порога — показывать «0%» новичку нельзя.
    accuracy: float | None = None
    control_tasks_count: int = 0


class MeOut(UserOut):
    email: EmailStr


class AuthResponse(Schema):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(
        default_factory=lambda: settings.ACCESS_TOKEN_TTL_MINUTES * 60
    )
    user: UserOut


class RefreshRequest(Schema):
    refresh_token: str
