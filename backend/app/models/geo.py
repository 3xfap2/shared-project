"""Территории, участки береговой линии и снимки ДЗЗ."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import SceneSource, SegmentStatus, Verdict

if TYPE_CHECKING:
    from app.models.annotation import Annotation
    from app.models.user import Subscription, User


class Oopt(Base, Timestamps):
    """Особо охраняемая природная территория.

    Идентификатор — читаемый slug (`sinie-ozera`), а не UUID: он попадает
    в URL и в выгрузки для отчётности, где UUID был бы неудобен.
    """

    __tablename__ = "oopts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_key: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Границы территории в GeoJSON. Нужны для генерации демо-карты
    # на этапе привлечения ООПТ — см. механику 15 в паспорте.
    boundary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    segments: Mapped[list["Segment"]] = relationship(
        back_populates="oopt", cascade="all, delete-orphan"
    )
    staff: Mapped[list["User"]] = relationship(back_populates="oopt")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Oopt {self.id}>"


class Segment(Base, Timestamps):
    """Участок береговой линии — основная единица работы системы.

    ФАКТОРЫ И ИНДЕКС ВНИМАНИЯ
    Индекс = 0.40·S + 0.30·C + 0.20·T + 0.10·A хранится denormalized,
    потому что по нему идёт сортировка карты приоритетов — главного экрана
    сотрудника ООПТ. Считать его выражением в ORDER BY означало бы полный
    перебор таблицы при каждом открытии кабинета.

    Единственное место пересчёта — `services.attention.recalculate()`.
    Присваивать `attention_index` где-либо ещё запрещено: рассинхрон
    факторов и индекса означает, что инспектор увидит неверный приоритет.
    """

    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    oopt_id: Mapped[str] = mapped_column(
        ForeignKey("oopts.id", ondelete="CASCADE"), nullable=False
    )
    name_key: Mapped[str] = mapped_column(String(120), nullable=False)
    length_km: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # GeoJSON LineString/Polygon. О переходе на PostGIS — см. db/base.py.
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Центр участка отдельными колонками. Нужен для выборки по области
    # карты: доставать геометрию из JSON и считать центр на каждый запрос
    # означало бы полный перебор таблицы при каждом сдвиге карты.
    #
    # Это первый реальный пространственный запрос в проекте — тот самый
    # случай, ради которого в db/base.py зарезервирован переход на PostGIS.
    # Пока участков сотни, двух индексированных колонок достаточно; когда
    # их станут десятки тысяч, здесь появится geometry(Point, 4326)
    # с GiST-индексом, а интерфейс запроса не изменится.
    center_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    center_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[SegmentStatus] = mapped_column(
        Enum(SegmentStatus, native_enum=False, length=20),
        default=SegmentStatus.WATCH,
        nullable=False,
    )

    # --- Факторы индекса внимания, каждый в [0, 1] ---
    factor_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    factor_c: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    factor_t: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    factor_a: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)

    # Готовое к показу значение 0..100.
    attention_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Консенсус и верификация ---
    votes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        doc="Момент попадания в очередь модерации ООПТ",
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        doc="Последняя подтверждённая проверка или уборка — источник фактора T",
    )

    # --- Эталонный пул для калибровки разметчиков ---
    # Участок с вынесенным решением инспектора можно переиспользовать как
    # скрытую проверку: правильный ответ по нему уже известен.
    is_control_pool: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    control_truth: Mapped[Verdict | None] = mapped_column(
        Enum(Verdict, native_enum=False, length=20), nullable=True,
        doc="Решение инспектора, служащее эталоном для контрольных заданий",
    )

    # --- Тип происшествия ---
    # Преобладающий вердикт разметчиков. Нужен карте для фильтра «по
    # происшествию»: инспектор ищет не «проблемные участки вообще», а
    # свалки отдельно от общей замусоренности — это разные бригады,
    # разная техника и разный порядок действий.
    incident_type: Mapped[Verdict | None] = mapped_column(
        Enum(Verdict, native_enum=False, length=20), nullable=True
    )

    # --- Динамика аномалии (детектор растущей свалки) ---
    # Основано на подтверждённом кейсе: по данным КА «Ресурс-П» фиксировался
    # именно РОСТ несанкционированной свалки у заповедника за 2017-2019.
    # Маленькая растущая свалка требует внимания раньше, чем большая
    # стабильная, поэтому рост усиливает фактор S внутри конвейера ДЗЗ,
    # а не добавляет пятое слагаемое в формулу индекса.
    growth_rate: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        doc="Прирост площади аномалии между двумя последними сценами, доля",
    )

    oopt: Mapped["Oopt"] = relationship(back_populates="segments")
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="segment",
        cascade="all, delete-orphan",
        order_by="Scene.captured_at.desc()",
    )
    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )
    subscribers: Mapped[list["Subscription"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Главный индекс продукта: карта приоритетов в кабинете ООПТ.
        Index("ix_segments_oopt_attention", "oopt_id", "attention_index"),
        Index("ix_segments_status", "status"),
        Index("ix_segments_incident", "incident_type"),
        CheckConstraint("factor_s >= 0 AND factor_s <= 1", name="factor_s_range"),
        CheckConstraint("factor_c >= 0 AND factor_c <= 1", name="factor_c_range"),
        CheckConstraint("factor_t >= 0 AND factor_t <= 1", name="factor_t_range"),
        CheckConstraint("factor_a >= 0 AND factor_a <= 1", name="factor_a_range"),
        CheckConstraint(
            "attention_index >= 0 AND attention_index <= 100",
            name="attention_index_range",
        ),
        CheckConstraint("votes >= 0", name="votes_non_negative"),
    )

    @property
    def is_growing(self) -> bool:
        """Растёт ли аномалия. Порог отсекает шум измерения."""
        from app.core.config import settings

        return (
            self.growth_rate is not None
            and self.growth_rate >= settings.GROWTH_ALERT_THRESHOLD
        )

    @property
    def votes_required(self) -> int:
        """Порог консенсуса. Живёт в настройках, а не в колонке: при росте
        шума его поднимают глобально (риск R-P3), а не по участкам."""
        from app.core.config import settings

        return settings.CONSENSUS_VOTES_REQUIRED

    @property
    def in_moderation_queue(self) -> bool:
        return self.queued_at is not None and not self.verified

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Segment {self.id} idx={self.attention_index} {self.status}>"


class Scene(Base, UUIDPrimaryKey, Timestamps):
    """Снимок ДЗЗ по участку.

    Модель хранит только метаданные и шаблон URL тайлов. Сами растры лежат
    в объектном хранилище и отдаются тайл-сервером — база не должна знать
    о пикселях.

    `captured_at` обязателен к показу в интерфейсе: пользователь должен
    понимать, что данные не в реальном времени (ограничение из паспорта).
    """

    __tablename__ = "scenes"

    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[SceneSource] = mapped_column(
        Enum(SceneSource, native_enum=False, length=20), nullable=False
    )
    resolution_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    tile_url_template: Mapped[str] = mapped_column(String(500), nullable=False)

    # Площадь выявленной аномалии на этой сцене. Заполняется конвейером ДЗЗ
    # (этап E4). Ряд таких значений по датам и даёт динамику роста.
    anomaly_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)

    segment: Mapped["Segment"] = relationship(back_populates="scenes")

    __table_args__ = (
        Index("ix_scenes_segment_captured", "segment_id", "captured_at"),
        CheckConstraint(
            "cloud_cover IS NULL OR (cloud_cover >= 0 AND cloud_cover <= 1)",
            name="cloud_cover_range",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scene {self.segment_id} {self.captured_at} {self.source}>"
