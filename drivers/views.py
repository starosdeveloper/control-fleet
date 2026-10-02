"""
Представления кабинета водителя (ТЗ §4, §5).

Шаг 1 реализует вход по активной паре ИНН+VIN, изоляцию одного VIN и выход.
Экран 1 показан в объёме, достаточном для проверки сквозного пути: VIN, дата
и пробег последнего отчёта, состояние ТО. Полные четыре экрана отправки — Шаг 2.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from fleet.access import resolve_active_assignment
from fleet.maintenance import maintenance_state
from fleet.models import Maintenance, OdometerReading, Report

from .forms import DriverLoginForm
from .ratelimit import is_locked, register_failure, reset
from .session import current_assignment, driver_required, end_session, start_session

# Единое сообщение об отказе (ТЗ §4): не раскрывает, что именно не совпало.
LOGIN_FAILED_MSG = (
    "Не удалось войти. Проверьте ИНН и VIN или обратитесь к менеджеру."
)
LOGIN_LOCKED_MSG = (
    "Слишком много попыток входа. Подождите несколько минут и попробуйте снова."
)


@require_http_methods(["GET", "POST"])
def driver_login(request):
    # Уже вошедшего водителя сразу ведём в кабинет.
    if current_assignment(request) is not None:
        return redirect("driver_home")

    form = DriverLoginForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            inn = form.cleaned_data["inn"]
            vin = form.cleaned_data["vin"]

            if is_locked(request, inn, vin):
                messages.error(request, LOGIN_LOCKED_MSG)
                return render(request, "drivers/login.html", {"form": form}, status=429)

            assignment = resolve_active_assignment(inn, vin)
            if assignment is not None:
                reset(request, inn, vin)
                request.session.cycle_key()  # защита от фиксации сессии
                start_session(request, assignment)
                return redirect("driver_home")

            register_failure(request, inn, vin)
            messages.error(request, LOGIN_FAILED_MSG)
        else:
            # Ошибка формата показывается у поля; единого сообщения об отказе не добавляем.
            pass

    return render(request, "drivers/login.html", {"form": form})


@require_http_methods(["POST"])
def driver_logout(request):
    end_session(request)
    return redirect("driver_login")


@driver_required
@require_http_methods(["GET"])
def driver_home(request):
    """Экран 1 (сокращённо для Шага 1): VIN, последний отчёт, состояние ТО."""
    vehicle = request.driver_vehicle

    last_report = (
        Report.objects.filter(vehicle=vehicle, state=Report.State.SUBMITTED)
        .order_by("-submitted_at")
        .first()
    )

    # Актуальный принятый пробег — самое новое по дате измерения среди принятых.
    accepted = (
        OdometerReading.objects.filter(
            vehicle=vehicle, verification=OdometerReading.Verification.ACCEPTED
        )
        .order_by("-measured_at", "-received_at")
        .first()
    )

    # База расчёта — пробег последнего ПОДТВЕРЖДЁННОГО ТО.
    confirmed = (
        Maintenance.objects.filter(
            vehicle=vehicle, status=Maintenance.Status.CONFIRMED
        )
        .order_by("-mileage_km")
        .first()
    )

    maint = maintenance_state(
        confirmed_maint_mileage=confirmed.mileage_km if confirmed else None,
        current_mileage=accepted.value_km if accepted else None,
        interval_km=vehicle.maintenance_interval_km,
        warning_km=settings.MAINTENANCE_WARNING_KM,
    )

    context = {
        "vehicle": vehicle,
        "assignment": request.driver_assignment,
        "last_report": last_report,
        "accepted": accepted,
        "maint": maint,
        "has_history": last_report is not None or accepted is not None,
    }
    return render(request, "drivers/home.html", context)
