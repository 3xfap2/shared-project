"""Ошибки API в формате контракта.

Каждая ошибка несёт машинный код и ключ локализации, а не готовый текст:
язык знает клиент. Формат зафиксирован в contract/openapi.yaml → ErrorResponse.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class APIError(HTTPException):
    """Доменная ошибка с кодом и ключом перевода."""

    def __init__(
        self,
        status_code: int,
        code: str,
        *,
        message_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message_key = message_key or f"err.{code}"
        self.details = details
        super().__init__(status_code=status_code, detail=code)


# --- Готовые ошибки: единый словарь кодов для всего приложения ---

def unauthorized(code: str = "unauthorized") -> APIError:
    return APIError(status.HTTP_401_UNAUTHORIZED, code)


def forbidden(code: str = "forbidden", **details: Any) -> APIError:
    return APIError(status.HTTP_403_FORBIDDEN, code, details=details or None)


def not_found(code: str = "not_found", **details: Any) -> APIError:
    return APIError(status.HTTP_404_NOT_FOUND, code, details=details or None)


def conflict(code: str, **details: Any) -> APIError:
    return APIError(status.HTTP_409_CONFLICT, code, details=details or None)


def unprocessable(code: str, **details: Any) -> APIError:
    return APIError(
        status.HTTP_422_UNPROCESSABLE_ENTITY, code, details=details or None
    )


async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message_key": exc.message_key,
            "details": exc.details,
        },
    )


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Ответы FastAPI по умолчанию приводим к тому же формату,
    чтобы фронт не разбирал два вида ошибок."""
    code = exc.detail if isinstance(exc.detail, str) else "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "message_key": f"err.{code}", "details": None},
    )
