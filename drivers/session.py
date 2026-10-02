"""
Управление водительской сессией (ТЗ §4).

ИНН и VIN не передаются в URL; в сессии храним только ID назначения/ТС.
Доступ ограничен одним VIN — тем, что введён при входе. Назначение
перепроверяется на каждом запросе: при смене арендатора старое назначение
закрывается и сессия немедленно теряет доступ. Срок жизни — 24 часа.
"""
from __future__ import annotations

from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone

from fleet.models import Assignment

_SESSION_ASSIGNMENT = "driver_assignment_id"
_SESSION_LOGIN_AT = "driver_login_at"


def start_session(request, assignment: Assignment) -> None:
    request.session[_SESSION_ASSIGNMENT] = assignment.id
    request.session[_SESSION_LOGIN_AT] = timezone.now().isoformat()
    request.session.set_expiry(settings.DRIVER_SESSION_MAX_AGE)


def end_session(request) -> None:
    for key in (_SESSION_ASSIGNMENT, _SESSION_LOGIN_AT):
        request.session.pop(key, None)


def current_assignment(request) -> Assignment | None:
    """
    Вернуть действующее назначение текущей водительской сессии или None.
    None означает: не входил, сессия истекла, или назначение больше не активно.
    """
    assignment_id = request.session.get(_SESSION_ASSIGNMENT)
    login_at_raw = request.session.get(_SESSION_LOGIN_AT)
    if not assignment_id or not login_at_raw:
        return None

    login_at = timezone.datetime.fromisoformat(login_at_raw)
    if timezone.now() - login_at > timedelta(seconds=settings.DRIVER_SESSION_MAX_AGE):
        return None

    return (
        Assignment.objects.select_related("vehicle", "tenant")
        .filter(id=assignment_id, ended_at__isnull=True, vehicle__archived_at__isnull=True)
        .first()
    )


def driver_required(view_func):
    """Декоратор: пускает только с действующей водительской сессией."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        assignment = current_assignment(request)
        if assignment is None:
            end_session(request)
            return redirect("driver_login")
        request.driver_assignment = assignment
        request.driver_vehicle = assignment.vehicle
        return view_func(request, *args, **kwargs)

    return wrapper
