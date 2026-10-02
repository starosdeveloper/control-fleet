"""
Проверка спорных показаний и ТО сотрудником (ТЗ §6, §7, §11).

Решение сотрудника имеет приоритет над автоматическим выбором (§6). Исправление
записи требует причины; исходное значение сохраняется в журнале (§7). Отклонение
ТО не меняет прежнюю базу расчёта (§11).

Доступ проверяет вызывающая сторона (view) через `access.can_staff_access_vehicle`;
здесь — только переходы состояний и аудит, атомарно.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import AuditLog, Maintenance, OdometerReading


def log_action(
    staff,
    action: str,
    obj,
    *,
    old_value: str = "",
    new_value: str = "",
    reason: str = "",
) -> AuditLog:
    """Записать административное действие (ТЗ §9, §10). Секретов и файлов не писать."""
    return AuditLog.objects.create(
        staff=staff if getattr(staff, "is_authenticated", False) else None,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        old_value=old_value,
        new_value=new_value,
        reason=reason,
    )


@transaction.atomic
def decide_reading(
    reading: OdometerReading, staff, accept: bool, reason: str = ""
) -> OdometerReading:
    """
    Принять или отклонить показание на проверке. Идемпотентно: повторное то же
    решение ничего не ломает. Решение сотрудника фиксируется и имеет приоритет.
    """
    locked = OdometerReading.objects.select_for_update().get(pk=reading.pk)
    old = locked.get_verification_display()
    locked.verification = (
        OdometerReading.Verification.ACCEPTED
        if accept
        else OdometerReading.Verification.REJECTED
    )
    locked.staff_decision_by = staff
    locked.decided_at = timezone.now()
    if reason:
        locked.note = reason
    locked.save(update_fields=["verification", "staff_decision_by", "decided_at", "note"])
    log_action(
        staff,
        "reading.accept" if accept else "reading.reject",
        locked,
        old_value=old,
        new_value=locked.get_verification_display(),
        reason=reason,
    )
    return locked


@transaction.atomic
def correct_reading(
    reading: OdometerReading, staff, new_value_km: int, reason: str
) -> OdometerReading:
    """
    Исправить значение пробега с обязательной причиной (ТЗ §7). Исходное значение
    сохраняется в журнале аудита. Исправление считается принятым решением сотрудника.
    """
    if not reason.strip():
        raise ValueError("Причина исправления обязательна (ТЗ §7).")
    locked = OdometerReading.objects.select_for_update().get(pk=reading.pk)
    old_value = locked.value_km
    locked.value_km = new_value_km
    locked.verification = OdometerReading.Verification.ACCEPTED
    locked.staff_decision_by = staff
    locked.decided_at = timezone.now()
    locked.save(
        update_fields=["value_km", "verification", "staff_decision_by", "decided_at"]
    )
    log_action(
        staff,
        "reading.correct",
        locked,
        old_value=f"{old_value} км",
        new_value=f"{new_value_km} км",
        reason=reason,
    )
    return locked


@transaction.atomic
def decide_maintenance(
    maint: Maintenance, staff, confirm: bool, reason: str = ""
) -> Maintenance:
    """
    Подтвердить или отклонить ТО на проверке (ТЗ §6, §11).

    Подтверждение делает ТО базой расчёта (следующий порог = пробег ТО + интервал).
    Отклонение прежнюю базу НЕ меняет: статус записи становится «отклонено», а
    ранее подтверждённые ТО остаются действующей базой.
    """
    locked = Maintenance.objects.select_for_update().get(pk=maint.pk)
    old = locked.get_status_display()
    if confirm:
        locked.status = Maintenance.Status.CONFIRMED
        locked.confirmed_by = staff
        locked.confirmed_at = timezone.now()
        locked.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    else:
        locked.status = Maintenance.Status.REJECTED
        locked.confirmed_by = staff
        locked.confirmed_at = timezone.now()
        if reason:
            locked.reason = reason
        locked.save(update_fields=["status", "confirmed_by", "confirmed_at", "reason"])
    log_action(
        staff,
        "maint.confirm" if confirm else "maint.reject",
        locked,
        old_value=old,
        new_value=locked.get_status_display(),
        reason=reason,
    )
    return locked
