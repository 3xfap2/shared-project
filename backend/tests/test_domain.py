"""Тесты доменной логики.

Она не требует базы, поэтому проверяется быстро и подробно. Это ядро
продукта: индекс внимания определяет, куда поедет инспектор, а контур
допуска — попадёт ли подросток на охраняемую территорию.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.annotation import Annotation
from app.models.enums import EnrollmentStatus, Requirement, Role, Verdict
from app.models.event import Consent, Enrollment
from app.models.geo import Segment
from app.models.user import User
from app.services import consensus, course, enrollment as enroll_svc
from app.services.attention import (
    Factors,
    apply_cleanup,
    compute_index,
    compute_recency,
    recalculate,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- #
# Индекс внимания
# ---------------------------------------------------------------- #

def test_index_zero_and_max():
    assert compute_index(Factors(0, 0, 0, 0)) == 0
    assert compute_index(Factors(1, 1, 1, 1)) == 100


def test_index_weights_match_passport():
    """Каждый фактор в одиночку даёт ровно свой вес."""
    assert compute_index(Factors(1, 0, 0, 0)) == 40
    assert compute_index(Factors(0, 1, 0, 0)) == 30
    assert compute_index(Factors(0, 0, 1, 0)) == 20
    assert compute_index(Factors(0, 0, 0, 1)) == 10


def test_satellite_signal_cannot_dominate():
    """Ключевое свойство для доверия ООПТ: спутник в одиночку не может
    поднять участок наверх. Максимум по S — 40 из 100."""
    only_satellite = compute_index(Factors(1.0, 0, 0, 0))
    people_and_time = compute_index(Factors(0, 1.0, 1.0, 0))
    assert only_satellite == 40
    assert people_and_time > only_satellite


def test_factors_reject_out_of_range():
    with pytest.raises(ValueError):
        Factors(1.5, 0, 0, 0)
    with pytest.raises(ValueError):
        Factors(0, -0.1, 0, 0)


def test_recency_grows_with_time():
    year_ago = NOW - timedelta(days=365)
    half_year = NOW - timedelta(days=182)
    assert compute_recency(NOW, now=NOW) == 0.0
    assert compute_recency(half_year, now=NOW) == pytest.approx(0.5, abs=0.01)
    assert compute_recency(year_ago, now=NOW) == 1.0


def test_recency_saturates_and_never_exceeds_one():
    ancient = NOW - timedelta(days=5000)
    assert compute_recency(ancient, now=NOW) == 1.0


def test_recency_without_any_reference_is_neutral():
    assert compute_recency(None, now=NOW, fallback=None) == 0.5


def test_recency_handles_naive_datetime():
    """Наивный datetime из внешнего источника не должен ронять расчёт."""
    naive = datetime(2026, 3, 5, 12, 0)
    assert 0.0 <= compute_recency(naive, now=NOW) <= 1.0


# ---------------------------------------------------------------- #
# Замыкание цикла
# ---------------------------------------------------------------- #

def _segment(**kw) -> Segment:
    defaults = dict(
        id="ustie",
        oopt_id="sinie-ozera",
        name_key="seg.ustie.name",
        factor_s=0.82,
        factor_c=0.73,
        factor_a=0.80,
        created_at=NOW - timedelta(days=200),
        last_verified_at=NOW - timedelta(days=255),
    )
    defaults.update(kw)
    return Segment(**defaults)


def test_recalculate_matches_prototype_reference():
    """Сверка с эталоном прототипа: после разметки индекс участка = 77."""
    seg = _segment()
    idx = recalculate(seg, now=NOW)
    assert idx == 77, f"ожидали 77 (эталон прототипа), получили {idx}"


def test_cleanup_closes_the_loop():
    """Главная проверка всей системы: подтверждённая уборка снижает
    приоритет участка, и он уходит вниз карты."""
    seg = _segment()
    before = recalculate(seg, now=NOW)

    after = apply_cleanup(seg, now=NOW)

    assert after < before, "после уборки индекс обязан снизиться"
    assert seg.factor_t == 0.0, "давность обнуляется — участок только что проверен"
    assert seg.last_verified_at == NOW
    assert seg.factor_c < 0.73, "проблема устранена — консенсус снижается"
    assert seg.factor_s < 0.82, "на следующем снимке аномалии ожидаемо не будет"


def test_cleanup_never_drives_factors_below_zero():
    seg = _segment(factor_s=0.05, factor_c=0.10)
    apply_cleanup(seg, now=NOW)
    assert seg.factor_s == 0.0
    assert seg.factor_c == 0.0
    assert seg.attention_index >= 0


def test_priority_order_flips_after_cleanup():
    """Модель поведения карты приоритетов целиком."""
    top = _segment(id="ustie")
    other = _segment(id="kosa", factor_s=0.61, factor_c=0.40, factor_a=0.60,
                     last_verified_at=NOW - timedelta(days=200))
    for s in (top, other):
        recalculate(s, now=NOW)
    assert top.attention_index > other.attention_index

    apply_cleanup(top, now=NOW)
    recalculate(other, now=NOW)
    assert other.attention_index > top.attention_index, (
        "после уборки приоритет обязан перейти на следующий участок"
    )


# ---------------------------------------------------------------- #
# Консенсус и репутация
# ---------------------------------------------------------------- #

def _annotation(verdict: Verdict, weight: float = 1.0) -> Annotation:
    return Annotation(user_id=None, segment_id="ustie", verdict=verdict, weight=weight)


def test_consensus_empty():
    r = consensus.evaluate([])
    assert r.votes == 0 and r.factor_c == 0.0 and not r.reached


def test_consensus_reached_at_threshold():
    marks = [_annotation(Verdict.DUMP) for _ in range(3)]
    r = consensus.evaluate(marks)
    assert r.votes == 3
    assert r.reached is True
    assert r.factor_c == pytest.approx(1.0)


def test_consensus_not_reached_below_threshold():
    r = consensus.evaluate([_annotation(Verdict.DUMP), _annotation(Verdict.DUMP)])
    assert r.votes == 2 and r.reached is False


def test_verdict_none_does_not_count_as_vote():
    marks = [_annotation(Verdict.DUMP), _annotation(Verdict.NONE), _annotation(Verdict.NONE)]
    r = consensus.evaluate(marks)
    assert r.votes == 1, "«проблемы нет» не подтверждает проблему"
    assert r.total == 3
    assert r.factor_c == pytest.approx(1 / 3)


def test_reputation_weights_the_consensus():
    """Разметка авторитетного участника весит больше, чем новичка."""
    trusted = consensus.evaluate(
        [_annotation(Verdict.DUMP, 1.5), _annotation(Verdict.NONE, 0.2)]
    )
    novice = consensus.evaluate(
        [_annotation(Verdict.DUMP, 0.2), _annotation(Verdict.NONE, 1.5)]
    )
    assert trusted.factor_c > novice.factor_c


def test_reputation_penalty_is_harsher_than_reward():
    """Случайная разметка должна становиться невыгодной быстрее,
    чем добросовестная — выгодной."""
    up = consensus.adjust_reputation(1.0, approved=True)
    down = consensus.adjust_reputation(1.0, approved=False)
    assert up - 1.0 == pytest.approx(0.10)
    assert 1.0 - down == pytest.approx(0.20)


def test_reputation_stays_within_bounds():
    low = 1.0
    for _ in range(50):
        low = consensus.adjust_reputation(low, approved=False)
    high = 1.0
    for _ in range(50):
        high = consensus.adjust_reputation(high, approved=True)
    assert low == pytest.approx(0.20)
    assert high == pytest.approx(1.50)


# ---------------------------------------------------------------- #
# Допуск на акцию — критично для безопасности
# ---------------------------------------------------------------- #

def _user(role: Role = Role.OBSERVER, birth_year: int = 2000) -> User:
    return User(
        email="a@b.ru", password_hash="x", name="Тест",
        birth_year=birth_year, role=role, reputation=1.0,
    )


def _enrollment(briefed: bool = False, consent: Consent | None = None) -> Enrollment:
    e = Enrollment(
        status=EnrollmentStatus.PENDING_REQUIREMENTS,
        briefing_completed_at=NOW if briefed else None,
    )
    e.consent = consent
    return e


def test_adult_needs_only_briefing():
    reqs = enroll_svc.evaluate_requirements(_user(), _enrollment())
    assert reqs == [Requirement.BRIEFING]


def test_adult_admitted_after_briefing():
    user, enr = _user(), _enrollment(briefed=True)
    assert enroll_svc.is_admitted(user, enr) is True


def test_student_blocked_until_course_done():
    reqs = enroll_svc.evaluate_requirements(_user(Role.STUDENT), _enrollment(briefed=True))
    assert Requirement.COURSE_COMPLETION in reqs


def test_minor_blocked_without_consent():
    minor = _user(birth_year=2010)          # ~16 лет на 2026
    assert minor.is_minor is True
    reqs = enroll_svc.evaluate_requirements(minor, _enrollment(briefed=True))
    assert Requirement.PARENT_CONSENT in reqs


def test_minor_with_requested_but_unsigned_consent_still_blocked():
    """Отправленный запрос — не согласие. Ключевая проверка безопасности."""
    minor = _user(birth_year=2010)
    unsigned = Consent(parent_contact="p@mail.ru", token="t", requested_at=NOW)
    reqs = enroll_svc.evaluate_requirements(minor, _enrollment(True, unsigned))
    assert Requirement.PARENT_CONSENT in reqs


def test_minor_admitted_only_with_signed_consent():
    minor = _user(birth_year=2010)
    signed = Consent(parent_contact="p@mail.ru", token="t", requested_at=NOW, signed_at=NOW)
    enr = _enrollment(briefed=True, consent=signed)
    assert enroll_svc.is_admitted(minor, enr) is True


def test_status_sync_and_terminal_states():
    user, enr = _user(), _enrollment()
    assert enroll_svc.sync_status(user, enr) == EnrollmentStatus.PENDING_REQUIREMENTS
    enr.briefing_completed_at = NOW
    assert enroll_svc.sync_status(user, enr) == EnrollmentStatus.READY

    enr.status = EnrollmentStatus.ATTENDED
    assert enroll_svc.sync_status(user, enr) == EnrollmentStatus.ATTENDED, (
        "терминальный статус не пересчитывается"
    )


# ---------------------------------------------------------------- #
# Курс
# ---------------------------------------------------------------- #

def test_course_answers_checked_server_side():
    m1 = course.get_module("m1")
    assert course.check_answer(m1, m1.correct_index) is True
    assert course.check_answer(m1, m1.correct_index + 1) is False


def test_course_completion_requires_all_modules():
    assert course.is_course_complete({"m1", "m2"}) is False
    assert course.is_course_complete({"m1", "m2", "m3"}) is True


def test_promotion_only_from_student():
    student = _user(Role.STUDENT)
    assert course.promote_on_completion(student) is True
    assert student.role == Role.OBSERVER

    staff = _user(Role.OOPT_STAFF)
    assert course.promote_on_completion(staff) is False, "роль сотрудника не понижаем"
    assert staff.role == Role.OOPT_STAFF


# ---------------------------------------------------------------- #
# Возраст и профиль
# ---------------------------------------------------------------- #

def test_minor_detection_by_birth_year():
    assert _user(birth_year=2010).is_minor is True     # ~16
    assert _user(birth_year=1995).is_minor is False    # ~31


def test_student_cannot_annotate_observer_can():
    assert _user(Role.STUDENT).can_annotate() is False
    assert _user(Role.OBSERVER).can_annotate() is True
    assert _user(Role.AMBASSADOR).can_annotate() is True


# ---------------------------------------------------------------- #
# Безопасность
# ---------------------------------------------------------------- #

def test_password_roundtrip():
    h = hash_password("Пароль-123")
    assert h != "Пароль-123"
    assert verify_password("Пароль-123", h) is True
    assert verify_password("другой", h) is False


def test_long_passwords_are_not_truncated():
    """bcrypt режет на 72 байтах. Предварительный SHA-256 снимает лимит:
    два длинных пароля, совпадающих в первых 72 байтах, различаются."""
    base = "и" * 100
    h = hash_password(base + "A")
    assert verify_password(base + "B", h) is False
    assert verify_password(base + "A", h) is True


def test_corrupted_hash_does_not_raise():
    assert verify_password("x", "не-хеш") is False


def test_access_token_carries_role():
    token = create_access_token("user-1", "observer")
    payload = decode_token(token, "access")
    assert payload["sub"] == "user-1"
    assert payload["role"] == "observer"


def test_refresh_token_rejected_where_access_expected():
    """Refresh-токен не должен открывать доступ к API."""
    refresh = create_refresh_token("user-1")
    with pytest.raises(TokenError):
        decode_token(refresh, "access")


def test_tampered_token_rejected():
    token = create_access_token("user-1", "observer")
    with pytest.raises(TokenError):
        decode_token(token[:-3] + "abc", "access")


def test_tokens_are_unique_per_issue():
    """jti различается — токены можно отзывать поштучно."""
    a = decode_token(create_access_token("u", "observer"), "access")
    b = decode_token(create_access_token("u", "observer"), "access")
    assert a["jti"] != b["jti"]
