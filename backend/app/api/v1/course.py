"""Обучающий контур: модули, прогресс, проверка ответов, выдача допуска.

Здесь происходит ключевой переход пользовательского пути: завершение курса
превращается в право работать с данными реальной территории. Поэтому
проверка ответов и повышение роли выполняются только на сервере.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.core import errors
from app.core.config import settings
from app.core.deps import CurrentUser, SessionDep
from app.db.base import utcnow
from app.models.learning import CourseProgress
from app.schemas.course import (
    AnswerRequest,
    AnswerResult,
    CourseModuleOut,
    CourseProgressOut,
)
from app.services import course as course_svc

router = APIRouter(prefix="/course", tags=["course"])


async def _completed_ids(session, user_id) -> set[str]:
    result = await session.execute(
        select(CourseProgress.module_id).where(
            CourseProgress.user_id == user_id,
            CourseProgress.completed_at.is_not(None),
        )
    )
    return set(result.scalars().all())


@router.get("/modules", response_model=list[CourseModuleOut], summary="Модули курса")
async def list_modules(user: CurrentUser, session: SessionDep) -> list[CourseModuleOut]:
    done = await _completed_ids(session, user.id)
    return [
        CourseModuleOut(
            id=m.id,
            order=m.order,
            title_key=m.title_key,
            theory_key=m.theory_key,
            question_key=m.question_key,
            options_count=m.options_count,
            duration_min=m.duration_min,
            completed=m.id in done,
        )
        for m in course_svc.MODULES
    ]


@router.get("/progress", response_model=CourseProgressOut, summary="Прогресс по курсу")
async def get_progress(user: CurrentUser, session: SessionDep) -> CourseProgressOut:
    result = await session.execute(
        select(CourseProgress).where(CourseProgress.user_id == user.id)
    )
    rows = list(result.scalars().all())
    done = [r.module_id for r in rows if r.is_completed]

    started = min((r.created_at for r in rows), default=None)
    finished = (
        max(r.completed_at for r in rows if r.completed_at)
        if course_svc.is_course_complete(set(done))
        else None
    )

    return CourseProgressOut(
        completed_modules=sorted(done),
        total_modules=course_svc.TOTAL_MODULES,
        course_completed=course_svc.is_course_complete(set(done)),
        started_at=started,
        completed_at=finished,
        certificate_id=user.certificate_id,
    )


@router.post(
    "/modules/{module_id}/answer",
    response_model=AnswerResult,
    summary="Ответ на практическое задание",
)
async def answer_module(
    module_id: str,
    payload: AnswerRequest,
    user: CurrentUser,
    session: SessionDep,
) -> AnswerResult:
    """Проверить ответ и, если курс завершён, выдать допуск.

    Правильный вариант клиенту не возвращается ни при каком исходе —
    иначе курс обходится подбором с раскрытым ответом.
    """
    module = course_svc.get_module(module_id)
    if module is None:
        raise errors.not_found("module_not_found")

    if payload.answer_index >= module.options_count:
        raise errors.unprocessable(
            "answer_out_of_range", options_count=module.options_count
        )

    progress = (
        await session.execute(
            select(CourseProgress).where(
                CourseProgress.user_id == user.id,
                CourseProgress.module_id == module_id,
            )
        )
    ).scalar_one_or_none()

    if progress is None:
        progress = CourseProgress(user_id=user.id, module_id=module_id, attempts=0)
        session.add(progress)

    # Курс — условие допуска в поле, а не формальность. Без ограничения он
    # проходится перебором за считанные запросы, и статус «Наблюдатель»
    # получает человек, не прочитавший раздел о технике безопасности.
    if progress.attempts >= settings.MAX_MODULE_ATTEMPTS:
        raise errors.APIError(
            429,
            "module_attempts_exhausted",
            details={
                "module_id": module_id,
                "max_attempts": settings.MAX_MODULE_ATTEMPTS,
            },
        )

    progress.attempts += 1
    correct = course_svc.check_answer(module, payload.answer_index)

    if not correct:
        await session.commit()
        return AnswerResult(correct=False, module_completed=False)

    # Повторный верный ответ не сбрасывает дату первого прохождения.
    if progress.completed_at is None:
        progress.completed_at = utcnow()

    await session.flush()

    done = await _completed_ids(session, user.id)
    done.add(module_id)
    course_completed = course_svc.is_course_complete(done)

    role_changed = None
    certificate = user.certificate_id

    if course_completed:
        if course_svc.promote_on_completion(user):
            role_changed = user.role
        if certificate is None:
            certificate = course_svc.make_certificate_id(
                str(user.id), utcnow().year
            )
            user.certificate_id = certificate

    await session.commit()

    return AnswerResult(
        correct=True,
        module_completed=True,
        course_completed=course_completed,
        role_changed_to=role_changed,
        certificate_id=certificate if course_completed else None,
    )
