"""
Регистрация моделей в штатной админке Django (ТЗ §2).

Менеджер видит в админке только закреплённые за ним ТС и связанные записи
(ТЗ §7). Ограничение реализовано на сервере через get_queryset — скрытие
кнопок интерфейса доступ бы не ограничило.
"""
from __future__ import annotations

from django.contrib import admin

from .access import is_admin, vehicles_for_staff
from .models import (
    Assignment,
    Attachment,
    AuditLog,
    ImportBatch,
    Maintenance,
    ManagerAssignment,
    OdometerReading,
    Report,
    Tenant,
    Vehicle,
)


class ManagerScopedAdmin(admin.ModelAdmin):
    """База для моделей, ограничиваемых областью видимости менеджера."""

    # Имя пути от модели к Vehicle для фильтра; у Vehicle — пустая строка.
    vehicle_path = "vehicle"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_admin(request.user):
            return qs
        allowed = vehicles_for_staff(request.user)
        if self.vehicle_path == "":
            return qs.filter(pk__in=allowed.values("pk"))
        return qs.filter(**{f"{self.vehicle_path}__in": allowed})


@admin.register(Vehicle)
class VehicleAdmin(ManagerScopedAdmin):
    vehicle_path = ""
    list_display = ("id", "vin", "plate", "model", "maintenance_interval_km", "status")
    search_fields = ("vin", "plate", "model")
    list_filter = ("archived_at",)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "inn", "created_at")
    search_fields = ("inn",)


@admin.register(Assignment)
class AssignmentAdmin(ManagerScopedAdmin):
    list_display = ("id", "vehicle", "tenant", "started_at", "ended_at", "is_active")
    list_filter = ("ended_at",)
    search_fields = ("vehicle__vin", "tenant__inn")


@admin.register(ManagerAssignment)
class ManagerAssignmentAdmin(ManagerScopedAdmin):
    vehicle_path = "assignment__vehicle"
    list_display = ("id", "assignment", "manager", "started_at", "ended_at")
    search_fields = ("assignment__vehicle__vin", "manager__username")


@admin.register(Report)
class ReportAdmin(ManagerScopedAdmin):
    list_display = ("id", "vehicle", "period_month", "state", "defects", "submitted_at")
    list_filter = ("state", "defects", "period_month")
    search_fields = ("vehicle__vin",)


@admin.register(OdometerReading)
class OdometerReadingAdmin(ManagerScopedAdmin):
    list_display = ("id", "vehicle", "value_km", "measured_at", "source", "verification")
    list_filter = ("source", "verification")
    search_fields = ("vehicle__vin",)


@admin.register(Maintenance)
class MaintenanceAdmin(ManagerScopedAdmin):
    list_display = ("id", "vehicle", "performed_on", "mileage_km", "status", "basis")
    list_filter = ("status", "basis")
    search_fields = ("vehicle__vin",)


@admin.register(Attachment)
class AttachmentAdmin(ManagerScopedAdmin):
    vehicle_path = "report__vehicle"
    list_display = ("id", "kind", "view", "content_type", "size_bytes", "uploaded_at")
    list_filter = ("kind",)


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "author", "created_at", "status")
    list_filter = ("status",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "staff", "action", "object_type", "object_id")
    list_filter = ("action",)
    search_fields = ("object_id", "action")
