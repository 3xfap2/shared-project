"""Точка входа приложения КОСМОБЕРЕГ."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    annotations,
    auth,
    course,
    events,
    export,
    games,
    imagery,
    moderation,
    public,
    reports,
    segments,
)
from app.core.config import settings
from app.core.errors import (
    APIError,
    api_error_handler,
    http_error_handler,
    validation_error_handler,
)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("kosmobereg")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Запуск %s (окружение: %s)", settings.APP_NAME, settings.ENV)
    if settings.is_sqlite and settings.ENV in ("staging", "production"):
        # SQLite не выдержит конкурентную запись из нескольких воркеров.
        logger.warning("SQLite в окружении %s — только для отладки", settings.ENV)
    yield
    logger.info("Остановка %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Бэкенд платформы КОСМОБЕРЕГ — обучение чтению снимков ДЗЗ, "
        "консенсусная разметка, модерация ООПТ и полевые отчёты.\n\n"
        "Реализует contract/openapi.yaml. Локализация выполняется на клиенте: "
        "API возвращает доменные коды и ключи словаря, а не переведённый текст."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ошибки приводятся к единому формату {code, message_key, details},
# чтобы фронт не разбирал два вида ответов.
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

api = APIRouter(prefix=settings.API_V1_PREFIX)
api.include_router(public.router)
api.include_router(auth.router)
api.include_router(course.router)
api.include_router(segments.router)
api.include_router(annotations.router)
api.include_router(moderation.router)
api.include_router(events.router)
api.include_router(reports.router)
api.include_router(imagery.router)
api.include_router(export.router)
api.include_router(games.router)
app.include_router(api)


@app.get("/health", tags=["service"], summary="Проверка живости")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.ENV}
