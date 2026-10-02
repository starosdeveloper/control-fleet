"""
Проверка полноты и отправка отчёта водителя (ТЗ §5, §6).

Отправка атомарна и идемпотентна: черновик переводится в «отправлен» той же
строкой (тот же номер отчёта), поэтому повторный запрос возвращает тот же отчёт
без дубликата. При частичной ошибке транзакция откатывается — отчёт не считается
отправленным (ТЗ §10).
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Attachment, Maintenance, OdometerReading, Report
from .readings import classify_reading

# Четыре обязательных ракурса тягача (ТЗ §5, экран 2).
REQUIRED_VIEWS = (
    Attachment.View.FRONT,
    Attachment.View.BACK,
    Attachment.View.LEFT,
    Attachment.View.RIGHT,
)


def completeness_errors(report: Report) -> list[str]:
    """
    Вернуть список незакрытых обязательных пунктов (ТЗ §5, экран 3).
    Пустой список — отчёт готов к отправке. Просрочка ТО отправку НЕ блокирует.
    """
    errors: list[str] = []

    if report.odometer_value_km is None:
        errors.append("Не введено показание одометра.")
    if not report.attachments.filter(kind=Attachment.Kind.DASHBOARD).exists():
        errors.append("Нет фото панели приборов с одометром.")

    have = set(
        report.attachments.filter(kind=Attachment.Kind.VEHICLE).values_list(
            "view", flat=True
        )
    )
    missing = [v.label for v in REQUIRED_VIEWS if v.value not in have]
    if missing:
        errors.append("Нет обязательных фото ТС: " + ", ".join(missing) + ".")

    if not report.maint_choice:
        errors.append("Не указаны сведения о ТО.")
    elif report.maint_choice == Report.MaintChoice.DONE:
        if report.maint_date is None or report.maint_mileage_km is None:
            errors.append("Для выполненного ТО нужны дата и пробег на ТО.")
        else:
            today = timezone.localdate()
            if report.maint_date > today:
                errors.append("Дата ТО не может быть позже сегодняшней.")
            if (
                report.odometer_value_km is not None
                and report.maint_mileage_km > report.odometer_value_km
            ):
                errors.append(
                    "Пробег на ТО не может превышать показание одометра в отчёте."
                )

    if report.defects == Report.Defects.HAS and not report.comment.strip():
        errors.append("При замечаниях комментарий обязателен.")

    return errors


def _classify_driver_reading(report: Report) -> tuple[str, str]:
    """Определить статус проверки нового показания водителя (ТЗ §6)."""
    vehicle = report.vehicle
    measured_date = timezone.localdate()
    value = report.odometer_value_km

    prior = list(
        vehicle.accepted_reading_rows().values_list("value_km", "measured_at")
    )
    same_date_other = list(
        OdometerReading.objects.filter(
            vehicle=vehicle,
            verification=OdometerReading.Verification.ACCEPTED,
            measured_at=measured_date,
        )
        .exclude(source=OdometerReading.Source.DRIVER)
        .values_list("value_km", flat=True)
    )
    verdict = classify_reading(
        new_value=value,
        new_date=measured_date,
        prior_accepted=prior,
        same_date_other_source=same_date_other,
        daily_jump_limit=settings.ODOMETER_DAILY_JUMP_KM,
        conflict_limit=settings.GPS_DRIVER_CONFLICT_KM,
    )
    if verdict.accepted:
        return OdometerReading.Verification.ACCEPTED, ""
    return OdometerReading.Verification.PENDING, verdict.reason


@transaction.atomic
def submit_report(report: Report) -> Report:
    """
    Отправить черновик. Возвращает отчёт. Если он уже отправлен — возвращает как
    есть (идемпотентность). Вызывать только после пустого completeness_errors().
    """
    # Блокируем строку: параллельный повторный POST увидит уже отправленный отчёт.
    locked = Report.objects.select_for_update().get(pk=report.pk)
    if locked.state == Report.State.SUBMITTED:
        return locked

    now = timezone.now()
    measured_date = timezone.localdate()

    verification, note = _classify_driver_reading(locked)
    OdometerReading.objects.create(
        vehicle=locked.vehicle,
        source=OdometerReading.Source.DRIVER,
        value_km=locked.odometer_value_km,
        measured_at=measured_date,
        received_at=now,
        report=locked,
        verification=verification,
        note=note,
    )

    if locked.maint_choice == Report.MaintChoice.DONE:
        # Факт сообщает водитель; подтверждает сотрудник. Статус «на проверке»,
        # действующую базу ТО не меняем (ТЗ §6).
        Maintenance.objects.create(
            vehicle=locked.vehicle,
            performed_on=locked.maint_date,
            mileage_km=locked.maint_mileage_km,
            status=Maintenance.Status.REPORTED,
            basis=Maintenance.Basis.DRIVER,
            work_order_no=locked.maint_work_order,
            report=locked,
            no_document=locked.maint_no_document,
            reason=locked.maint_reason,
        )

    locked.manager_at_report = locked.assignment.manager
    locked.measured_at = now
    locked.submitted_at = now
    locked.state = Report.State.SUBMITTED
    locked.current_step = 4
    locked.save()
    return locked
