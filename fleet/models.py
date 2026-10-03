"""
Доменные модели системы контроля грузового парка (ТЗ §9).

В базе — связанные записи; фотографии и документы — в закрытом файловом
хранилище (модель Attachment хранит только путь и метаданные). Неизменяемый
ID ТС (PK Vehicle) — основа для будущих платежей, штрафов и уведомлений;
новые функции не привязывать к тексту VIN (ТЗ §10).
"""
from __future__ import annotations

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .validators import validate_inn, validate_vin


# --- Арендатор и ТС -----------------------------------------------------------

class Tenant(models.Model):
    """Арендатор. ИНН строкой; ФИО водителя не хранится (ТЗ §4, §9)."""

    inn = models.CharField(
        "ИНН", max_length=12, unique=True, validators=[validate_inn]
    )
    created_at = models.DateTimeField("создан", default=timezone.now)

    class Meta:
        verbose_name = "арендатор"
        verbose_name_plural = "арендаторы"

    def __str__(self) -> str:
        return f"ИНН {self.inn}"


class Vehicle(models.Model):
    """Транспортное средство. ID неизменяем, VIN уникален (ТЗ §9)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "в работе"
        ARCHIVED = "archived", "в архиве"

    vin = models.CharField(
        "VIN", max_length=17, unique=True, validators=[validate_vin]
    )
    plate = models.CharField("госномер", max_length=20, blank=True)
    model = models.CharField("модель", max_length=120, blank=True)
    vehicle_type = models.CharField("тип", max_length=60, blank=True)
    maintenance_interval_km = models.PositiveIntegerField(
        "интервал ТО, км", default=settings.MAINTENANCE_INTERVAL_KM
    )
    created_at = models.DateTimeField("создан", default=timezone.now)
    archived_at = models.DateTimeField("архивирован", null=True, blank=True)

    class Meta:
        verbose_name = "ТС"
        verbose_name_plural = "ТС"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.vin

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def status(self) -> str:
        return self.Status.ARCHIVED if self.is_archived else self.Status.ACTIVE

    def get_status_display(self) -> str:
        """
        Человекочитаемый статус. `status` — Python-property (не поле БД), поэтому
        Django не создаёт этот метод автоматически, как для обычных choices-полей;
        без него шаблон `{{ vehicle.get_status_display }}` молча рендерит пусто.
        """
        return self.Status(self.status).label

    def active_assignment(self) -> "Assignment | None":
        return self.assignments.filter(ended_at__isnull=True).first()

    def folder_name(self) -> str:
        """Каталог файлов «ID — VIN» (ТЗ §9). ID неизменяем, VIN подставляется реальный."""
        return f"{self.id:06d}_{self.vin}"

    def accepted_reading_rows(self):
        """
        Принятые показания, учитываемые в расчёте: без показаний из ОТМЕНЁННЫХ
        партий импорта (ТЗ §8 — отменённые остаются в истории, но из расчёта
        исключаются). Строки с `import_batch IS NULL` (водитель, ручной ввод)
        остаются.
        """
        return self.readings.filter(verification="accepted").exclude(
            import_batch__status="reverted"
        )


# --- Назначения ---------------------------------------------------------------

class Assignment(models.Model):
    """
    Назначение аренды: ТС ↔ арендатор на интервал времени (ТЗ §9).
    Не более одного действующего назначения на ТС (ended_at IS NULL).
    """

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="assignments",
        verbose_name="ТС",
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name="assignments",
        verbose_name="арендатор",
    )
    started_at = models.DateTimeField("начало", default=timezone.now)
    ended_at = models.DateTimeField("конец", null=True, blank=True)

    class Meta:
        verbose_name = "назначение аренды"
        verbose_name_plural = "назначения аренды"
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_active_assignment_per_vehicle",
            )
        ]

    def __str__(self) -> str:
        state = "действует" if self.is_active else "закрыто"
        return f"{self.vehicle.vin} ← {self.tenant.inn} ({state})"

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    def active_manager_assignment(self) -> "ManagerAssignment | None":
        return self.manager_assignments.filter(ended_at__isnull=True).first()

    @property
    def manager(self):
        ma = self.active_manager_assignment()
        return ma.manager if ma else None


class ManagerAssignment(models.Model):
    """
    Закрепление менеджера за назначением аренды (ТЗ §9).
    История сохраняется; активных закреплений на одно назначение — не более одного.
    """

    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="manager_assignments",
        verbose_name="назначение аренды",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="managed_assignments", verbose_name="менеджер",
    )
    started_at = models.DateTimeField("начало закрепления", default=timezone.now)
    ended_at = models.DateTimeField("конец закрепления", null=True, blank=True)

    class Meta:
        verbose_name = "закрепление менеджера"
        verbose_name_plural = "закрепления менеджеров"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_active_manager_per_assignment",
            )
        ]

    def __str__(self) -> str:
        return f"{self.manager} → {self.assignment.vehicle.vin}"


# --- Отчёт водителя -----------------------------------------------------------

class Report(models.Model):
    """Месячный отчёт водителя (ТЗ §5, §9)."""

    class State(models.TextChoices):
        DRAFT = "draft", "черновик"
        SUBMITTED = "submitted", "отправлен"

    class Defects(models.TextChoices):
        NONE = "none", "замечаний нет"
        HAS = "has", "есть замечания"

    class MaintChoice(models.TextChoices):
        DONE = "done", "ТО выполнено"
        NOT_DONE = "not_done", "ТО не выполнено"
        UNKNOWN = "unknown", "Не знаю / нет документов"

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="reports", verbose_name="ТС"
    )
    assignment = models.ForeignKey(
        Assignment, on_delete=models.PROTECT, related_name="reports",
        verbose_name="назначение",
    )
    manager_at_report = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reports_as_manager", verbose_name="менеджер на дату отчёта",
    )
    # Первое число месяца, к которому относится отчёт.
    period_month = models.DateField("отчётный месяц")
    state = models.CharField(
        "состояние", max_length=12, choices=State.choices, default=State.DRAFT
    )
    defects = models.CharField(
        "замечания", max_length=8, choices=Defects.choices, default=Defects.NONE
    )
    comment = models.TextField("комментарий", max_length=2000, blank=True)
    measured_at = models.DateTimeField("время измерения", null=True, blank=True)
    submitted_at = models.DateTimeField("время отправки", null=True, blank=True)
    created_at = models.DateTimeField("создан", default=timezone.now)

    # --- Черновиковые поля экранов водителя (§5); замораживаются при отправке ---
    current_step = models.PositiveSmallIntegerField("текущий экран", default=1)
    odometer_value_km = models.PositiveIntegerField(
        "показание одометра, км", null=True, blank=True
    )
    maint_choice = models.CharField(
        "сведения о ТО", max_length=10, choices=MaintChoice.choices, blank=True
    )
    maint_date = models.DateField("дата ТО", null=True, blank=True)
    maint_mileage_km = models.PositiveIntegerField("пробег на ТО, км", null=True, blank=True)
    maint_work_order = models.CharField("заказ-наряд", max_length=120, blank=True)
    maint_no_document = models.BooleanField("нет документа ТО", default=False)
    maint_reason = models.TextField("причина отсутствия документа", blank=True)
    # Ключ защиты от повторной отправки (ТЗ §4, §5, §9).
    idempotency_key = models.CharField(
        "ключ идемпотентности", max_length=64, unique=True, null=True, blank=True
    )

    class Meta:
        verbose_name = "отчёт"
        verbose_name_plural = "отчёты"
        ordering = ["-created_at"]
        constraints = [
            # Не более одного черновика на назначение (ТЗ §5).
            models.UniqueConstraint(
                fields=["assignment"],
                condition=models.Q(state="draft"),
                name="unique_draft_per_assignment",
            )
        ]

    def __str__(self) -> str:
        return f"Отчёт №{self.id} {self.vehicle.vin} {self.period_month:%Y-%m}"

    @property
    def number(self) -> str:
        return f"R-{self.id:06d}"


# --- Показания одометра -------------------------------------------------------

class OdometerReading(models.Model):
    """Показание пробега с источником и статусом проверки (ТЗ §6, §8, §9)."""

    class Source(models.TextChoices):
        DRIVER = "driver", "водитель"
        GPS = "gps", "GPS"
        MANUAL = "manual", "ручной ввод"

    class Verification(models.TextChoices):
        ACCEPTED = "accepted", "принято"
        PENDING = "pending", "на проверке"
        REJECTED = "rejected", "отклонено"

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="readings", verbose_name="ТС"
    )
    source = models.CharField("источник", max_length=8, choices=Source.choices)
    value_km = models.PositiveIntegerField(
        "пробег, км", validators=[MinValueValidator(0)]
    )
    measured_at = models.DateField("дата измерения")
    received_at = models.DateTimeField("время получения", default=timezone.now)
    report = models.ForeignKey(
        Report, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="readings", verbose_name="отчёт",
    )
    import_batch = models.ForeignKey(
        "ImportBatch", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="readings", verbose_name="партия импорта",
    )
    verification = models.CharField(
        "проверка", max_length=10, choices=Verification.choices,
        default=Verification.PENDING,
    )
    # Решение сотрудника имеет приоритет над автоматическим выбором (ТЗ §6).
    staff_decision_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="odometer_decisions", verbose_name="решение сотрудника",
    )
    decided_at = models.DateTimeField("время решения", null=True, blank=True)
    note = models.TextField("примечание", blank=True)

    class Meta:
        verbose_name = "показание пробега"
        verbose_name_plural = "показания пробега"
        ordering = ["-measured_at", "-received_at"]
        indexes = [models.Index(fields=["vehicle", "measured_at"])]

    def __str__(self) -> str:
        return f"{self.vehicle.vin}: {self.value_km} км @ {self.measured_at} ({self.source})"


# --- Техническое обслуживание -------------------------------------------------

class Maintenance(models.Model):
    """Запись о ТО с документами и подтверждением (ТЗ §6, §9)."""

    class Status(models.TextChoices):
        REPORTED = "reported", "на проверке"
        CONFIRMED = "confirmed", "подтверждено"
        REJECTED = "rejected", "отклонено"

    class Basis(models.TextChoices):
        DRIVER = "driver", "сообщил водитель"
        INITIAL = "initial", "начальная база"
        STAFF = "staff", "ввод сотрудника"

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, related_name="maintenances", verbose_name="ТС"
    )
    performed_on = models.DateField("дата ТО")
    mileage_km = models.PositiveIntegerField("пробег на ТО, км")
    status = models.CharField(
        "статус", max_length=10, choices=Status.choices, default=Status.REPORTED
    )
    basis = models.CharField("основание", max_length=10, choices=Basis.choices)
    work_order_no = models.CharField("заказ-наряд", max_length=120, blank=True)
    report = models.ForeignKey(
        Report, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maintenances", verbose_name="отчёт",
    )
    no_document = models.BooleanField("нет документа", default=False)
    reason = models.TextField("обоснование", blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maintenance_confirmations", verbose_name="кто подтвердил",
    )
    confirmed_at = models.DateTimeField("когда подтверждено", null=True, blank=True)
    created_at = models.DateTimeField("создано", default=timezone.now)

    class Meta:
        verbose_name = "ТО"
        verbose_name_plural = "ТО"
        ordering = ["-performed_on", "-mileage_km"]
        indexes = [models.Index(fields=["vehicle", "status", "mileage_km"])]

    def __str__(self) -> str:
        return f"ТО {self.vehicle.vin} {self.performed_on} {self.mileage_km} км [{self.status}]"


# --- Вложения -----------------------------------------------------------------

class Attachment(models.Model):
    """
    Вложение в закрытом хранилище (ТЗ §9). В БД — только путь и метаданные;
    публичных постоянных ссылок нет, раздача после проверки прав.
    """

    class Kind(models.TextChoices):
        DASHBOARD = "dashboard", "панель приборов (одометр)"
        VEHICLE = "vehicle", "фото ТС (ракурс)"
        DEFECT = "defect", "дефект"
        WORK_ORDER = "work_order", "заказ-наряд"
        SERVICE_BOOK = "service_book", "сервисная книжка"

    class View(models.TextChoices):
        FRONT = "front", "спереди"
        BACK = "back", "сзади"
        LEFT = "left", "слева"
        RIGHT = "right", "справа"
        NONE = "none", "—"

    report = models.ForeignKey(
        Report, on_delete=models.CASCADE, null=True, blank=True,
        related_name="attachments", verbose_name="отчёт",
    )
    maintenance = models.ForeignKey(
        Maintenance, on_delete=models.CASCADE, null=True, blank=True,
        related_name="attachments", verbose_name="ТО",
    )
    kind = models.CharField("тип", max_length=16, choices=Kind.choices)
    view = models.CharField(
        "ракурс", max_length=8, choices=View.choices, default=View.NONE
    )
    # Относительный путь внутри закрытого хранилища; имя генерирует сервер (ТЗ §9).
    stored_path = models.CharField("путь в хранилище", max_length=512)
    content_type = models.CharField("тип файла", max_length=100)
    size_bytes = models.PositiveBigIntegerField("размер, байт")
    uploaded_at = models.DateTimeField("время загрузки", default=timezone.now)

    class Meta:
        verbose_name = "вложение"
        verbose_name_plural = "вложения"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} ({self.stored_path})"

    @property
    def vehicle(self) -> "Vehicle | None":
        if self.report_id:
            return self.report.vehicle
        if self.maintenance_id:
            return self.maintenance.vehicle
        return None


# --- Импорт и аудит -----------------------------------------------------------

class ImportBatch(models.Model):
    """Партия импорта показаний GPS из Excel (ТЗ §8, §9)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "применена"
        REVERTED = "reverted", "отменена"

    source_path = models.CharField("исходный файл", max_length=512)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="import_batches", verbose_name="автор импорта",
    )
    created_at = models.DateTimeField("дата импорта", default=timezone.now)
    status = models.CharField(
        "статус", max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    summary = models.JSONField("итог проверки", default=dict, blank=True)
    reverted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reverted_batches", verbose_name="кто отменил",
    )
    reverted_at = models.DateTimeField("когда отменена", null=True, blank=True)
    revert_reason = models.TextField("причина отмены", blank=True)

    class Meta:
        verbose_name = "партия импорта"
        verbose_name_plural = "партии импорта"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Импорт №{self.id} ({self.created_at:%Y-%m-%d})"


class AuditLog(models.Model):
    """Журнал административных действий (ТЗ §9, §10)."""

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="audit_entries", verbose_name="сотрудник",
    )
    action = models.CharField("действие", max_length=120)
    object_type = models.CharField("тип объекта", max_length=80, blank=True)
    object_id = models.CharField("ID объекта", max_length=64, blank=True)
    old_value = models.TextField("старое значение", blank=True)
    new_value = models.TextField("новое значение", blank=True)
    reason = models.TextField("причина", blank=True)
    created_at = models.DateTimeField("время", default=timezone.now)

    class Meta:
        verbose_name = "запись аудита"
        verbose_name_plural = "журнал аудита"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
