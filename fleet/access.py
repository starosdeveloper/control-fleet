"""
Изоляция доступа (ТЗ §4, §7).

Права проверяются для каждой страницы, операции и скачивания. Скрытая кнопка
в интерфейсе не заменяет проверку — весь контроль на сервере.

Роли:
    администратор — весь парк (is_superuser либо группа «Администраторы»);
    менеджер      — только закреплённые за ним ТС (группа «Менеджеры»).

Рабочая очередь менеджера относится к ТЕКУЩЕМУ закреплению (ended_at IS NULL).
Историческая принадлежность отчёта — к менеджеру на дату события, и хранится
на самом отчёте (Report.manager_at_report), а не выводится из текущего закрепления.
"""
from __future__ import annotations

from django.db.models import QuerySet

from .models import Assignment, Vehicle

ADMIN_GROUP = "Администраторы"
MANAGER_GROUP = "Менеджеры"


def is_admin(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_superuser or user.groups.filter(name=ADMIN_GROUP).exists())


def is_manager(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name=MANAGER_GROUP).exists()


def vehicles_for_staff(user) -> QuerySet[Vehicle]:
    """
    ТС, видимые сотруднику. Администратор — все; менеджер — только с активным
    закреплением за ним через действующее назначение аренды. Посторонний
    сотрудник не видит ничего.
    """
    if is_admin(user):
        return Vehicle.objects.all()
    if is_manager(user):
        return Vehicle.objects.filter(
            assignments__ended_at__isnull=True,
            assignments__manager_assignments__ended_at__isnull=True,
            assignments__manager_assignments__manager=user,
        ).distinct()
    return Vehicle.objects.none()


def can_staff_access_vehicle(user, vehicle: Vehicle) -> bool:
    """Проверка доступа сотрудника к конкретному ТС (для страниц и скачиваний)."""
    if is_admin(user):
        return True
    if is_manager(user):
        return vehicles_for_staff(user).filter(pk=vehicle.pk).exists()
    return False


def resolve_active_assignment(inn: str, vin: str) -> Assignment | None:
    """
    Вход водителя (ТЗ §4): проверяется не отдельное существование ИНН и VIN, а
    АКТИВНОЕ назначение именно этой пары. Значения должны быть уже нормализованы.
    Возвращает действующее назначение или None.
    """
    return (
        Assignment.objects.select_related("vehicle", "tenant")
        .filter(
            ended_at__isnull=True,
            tenant__inn=inn,
            vehicle__vin=vin,
            vehicle__archived_at__isnull=True,
        )
        .first()
    )
