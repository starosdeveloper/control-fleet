"""
Кабинет сотрудников (ТЗ §7): дашборд месяца, таблица контроля с фильтрами,
карточка VIN, проверка спорных показаний и ТО, экспорт в Excel и ZIP.

Вся логика среза, расчёта и сравнения — в крейтах fleet.dashboard/asof/
maintenance/metrics (проверены на хосте). Здесь — HTTP, область видимости и
действия сотрудника. Права проверяются на сервере для каждой страницы и операции
(ТЗ §4): posторонний сотрудник не видит ничего, менеджер — только свой парк.
"""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from datetime import datetime

from fleet import dashboard as dash
from fleet import exports, gpsservice, onboardingservice, review
from fleet.models import ImportBatch
from fleet.storage import save_import_file
from fleet.access import (
    MANAGER_GROUP,
    can_staff_access_vehicle,
    is_admin,
    is_manager,
)
from fleet.fileaccess import serve_attachment
from fleet.maintenance import maintenance_state
from fleet.models import (
    AuditLog,
    Maintenance,
    OdometerReading,
    Report,
    Vehicle,
)
from fleet.asof import accepted_reading_asof, confirmed_maint_asof
from django.conf import settings


def staff_required(view):
    """Допуск к кабинету: вошедший администратор или менеджер. Иначе — вход/404."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            login_url = reverse("admin:login")
            return redirect(f"{login_url}?next={request.path}")
        if not (is_admin(user) or is_manager(user)):
            raise Http404  # posторонний сотрудник не видит ничего (ТЗ §4)
        return view(request, *args, **kwargs)

    return wrapped


def admin_required(view):
    """Допуск только администратору (ТЗ §1, §8: Excel загружает администратор)."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            login_url = reverse("admin:login")
            return redirect(f"{login_url}?next={request.path}")
        if not is_admin(user):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped


def _manager_filter(request) -> int | None:
    """ID менеджера из query (только для администратора). Для менеджера — None."""
    if not is_admin(request.user):
        return None
    raw = request.GET.get("manager")
    if raw and raw.isdigit():
        return int(raw)
    return None


def _table_filters(request) -> dict:
    return {
        "q": request.GET.get("q", ""),
        "flag": request.GET.get("flag", ""),
    }


@staff_required
@require_http_methods(["GET"])
def dashboard_view(request):
    today = timezone.localdate()
    manager_id = _manager_filter(request)
    filters = _table_filters(request)
    data = dash.build_dashboard(
        request.user,
        request.GET.get("month"),
        manager_id,
        filters,
        today,
    )

    managers = []
    if is_admin(request.user):
        User = get_user_model()
        managers = list(
            User.objects.filter(groups__name=MANAGER_GROUP)
            .order_by("username")
            .values("id", "username")
        )

    # Querystring без параметра страницы/экспорта — для ссылок «экспорт» и фильтров.
    context = {
        "data": data,
        "managers": managers,
        "is_admin": is_admin(request.user),
        "month_value": f"{data.month_first:%Y-%m}",
        "filters": filters,
        "manager_id": manager_id,
        "months": _recent_months(today),
    }
    return render(request, "staff/dashboard.html", context)


def _recent_months(today) -> list[dict]:
    """Последние 13 месяцев для выпадающего выбора (YYYY-MM + подпись)."""
    out = []
    first = today.replace(day=1)
    for n in range(13):
        m = dash.months_back(first, n)
        out.append({"value": f"{m:%Y-%m}", "label": f"{m:%m.%Y}"})
    return out


@staff_required
@require_http_methods(["GET"])
def vehicle_card(request, vehicle_id: int):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    if not can_staff_access_vehicle(request.user, vehicle):
        raise Http404  # чужой VIN сотруднику не открыть прямым запросом (ТЗ §4, §11)

    today = timezone.localdate()

    assignments = list(
        vehicle.assignments.select_related("tenant")
        .prefetch_related("manager_assignments__manager")
        .order_by("-started_at")
    )
    active_assignment = next((a for a in assignments if a.is_active), None)

    reports = list(
        vehicle.reports.filter(state=Report.State.SUBMITTED)
        .prefetch_related("attachments")
        .order_by("-period_month", "-submitted_at")
    )
    readings = list(
        vehicle.readings.select_related("staff_decision_by").order_by(
            "-measured_at", "-received_at"
        )
    )
    maintenances = list(
        vehicle.maintenances.select_related("confirmed_by").order_by(
            "-performed_on", "-mileage_km"
        )
    )

    # Текущее состояние ТО (на сегодня): база — последнее подтверждённое ТО,
    # актуальный пробег — последнее принятое показание.
    accepted = [
        (r.value_km, r.measured_at, r.source)
        for r in readings
        if r.verification == OdometerReading.Verification.ACCEPTED
    ]
    confirmed = [
        (m.mileage_km, m.performed_on)
        for m in maintenances
        if m.status == Maintenance.Status.CONFIRMED
    ]
    cur_reading = accepted_reading_asof(accepted, today)
    cur_base = confirmed_maint_asof(confirmed, today)
    maint = maintenance_state(
        confirmed_maint_mileage=cur_base[0] if cur_base else None,
        current_mileage=cur_reading[0] if cur_reading else None,
        interval_km=vehicle.maintenance_interval_km,
        warning_km=settings.MAINTENANCE_WARNING_KM,
    )

    pending_readings = [
        r for r in readings if r.verification == OdometerReading.Verification.PENDING
    ]
    pending_maints = [
        m for m in maintenances if m.status == Maintenance.Status.REPORTED
    ]

    # Журнал исправлений по записям этого ТС (ТЗ §7).
    reading_ids = {str(r.pk) for r in readings}
    maint_ids = {str(m.pk) for m in maintenances}
    report_ids = {str(r.pk) for r in reports}
    audit = [
        e
        for e in AuditLog.objects.select_related("staff").order_by("-created_at")[:200]
        if (e.object_type == "OdometerReading" and e.object_id in reading_ids)
        or (e.object_type == "Maintenance" and e.object_id in maint_ids)
        or (e.object_type == "Report" and e.object_id in report_ids)
    ]

    context = {
        "vehicle": vehicle,
        "active_assignment": active_assignment,
        "assignments": assignments,
        "reports": [_report_block(r) for r in reports],
        "readings": readings,
        "maintenances": maintenances,
        "maint": maint,
        "cur_reading": cur_reading,
        "pending_readings": pending_readings,
        "pending_maints": pending_maints,
        "audit": audit,
        "back": request.META.get("HTTP_REFERER", reverse("staff_dashboard")),
    }
    return render(request, "staff/vehicle_card.html", context)


def _report_block(report: Report) -> dict:
    """Сгруппировать вложения отчёта для показа в карточке."""
    from fleet.models import Attachment

    atts = list(report.attachments.all())
    return {
        "report": report,
        "dashboard": [a for a in atts if a.kind == Attachment.Kind.DASHBOARD],
        "views": [a for a in atts if a.kind == Attachment.Kind.VEHICLE],
        "defects": [a for a in atts if a.kind == Attachment.Kind.DEFECT],
        "docs": [
            a
            for a in atts
            if a.kind in (Attachment.Kind.WORK_ORDER, Attachment.Kind.SERVICE_BOOK)
        ],
    }


# --- Действия сотрудника ------------------------------------------------------

def _require_vehicle_access(request, vehicle) -> None:
    if not can_staff_access_vehicle(request.user, vehicle):
        raise Http404


@staff_required
@require_http_methods(["POST"])
def reading_decide(request, reading_id: int):
    reading = get_object_or_404(
        OdometerReading.objects.select_related("vehicle"), pk=reading_id
    )
    _require_vehicle_access(request, reading.vehicle)

    action = request.POST.get("action")
    reason = request.POST.get("reason", "").strip()

    if action == "accept":
        review.decide_reading(reading, request.user, accept=True, reason=reason)
        messages.success(request, f"Показание {reading.value_km} км принято.")
    elif action == "reject":
        review.decide_reading(reading, request.user, accept=False, reason=reason)
        messages.success(request, "Показание отклонено.")
    elif action == "correct":
        raw = request.POST.get("new_value", "").strip()
        if not raw.isdigit():
            messages.error(request, "Введите целое число километров.")
        elif not reason:
            messages.error(request, "Укажите причину исправления.")
        else:
            try:
                review.correct_reading(reading, request.user, int(raw), reason)
                messages.success(request, f"Пробег исправлен на {raw} км.")
            except ValueError as exc:
                messages.error(request, str(exc))
    else:
        messages.error(request, "Неизвестное действие.")

    return redirect("staff_vehicle_card", vehicle_id=reading.vehicle_id)


@staff_required
@require_http_methods(["POST"])
def maintenance_decide(request, maint_id: int):
    maint = get_object_or_404(
        Maintenance.objects.select_related("vehicle"), pk=maint_id
    )
    _require_vehicle_access(request, maint.vehicle)

    action = request.POST.get("action")
    reason = request.POST.get("reason", "").strip()

    if action == "confirm":
        review.decide_maintenance(maint, request.user, confirm=True, reason=reason)
        messages.success(
            request,
            f"ТО подтверждено. Следующий порог {maint.mileage_km + maint.vehicle.maintenance_interval_km} км.",
        )
    elif action == "reject":
        review.decide_maintenance(maint, request.user, confirm=False, reason=reason)
        messages.success(request, "ТО отклонено. Прежняя база расчёта сохранена.")
    else:
        messages.error(request, "Неизвестное действие.")

    return redirect("staff_vehicle_card", vehicle_id=maint.vehicle_id)


# --- Экспорт ------------------------------------------------------------------

@staff_required
@require_http_methods(["GET"])
def export_table_xlsx(request):
    today = timezone.localdate()
    manager_id = _manager_filter(request)
    filters = _table_filters(request)
    data = dash.build_dashboard(
        request.user, request.GET.get("month"), manager_id, filters, today
    )
    blob = exports.control_table_xlsx(data.rows, data.month_first)
    resp = HttpResponse(
        blob,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = (
        f'attachment; filename="control_{data.month_first:%Y-%m}.xlsx"'
    )
    return resp


@staff_required
@require_http_methods(["GET"])
def export_report_zip(request, report_id: int):
    report = get_object_or_404(
        Report.objects.select_related("vehicle").prefetch_related("attachments"),
        pk=report_id,
    )
    _require_vehicle_access(request, report.vehicle)
    blob = exports.report_zip(report)
    resp = HttpResponse(blob, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{report.number}.zip"'
    return resp


# --- Закрытая раздача файлов сотруднику (ТЗ §9) -------------------------------

# --- GPS-импорт из Excel (ТЗ §8) ---------------------------------------------

def _mapping_from(request) -> dict:
    return {
        "vin": request.POST.get("map_vin", gpsservice.DEFAULT_MAP["vin"]).strip()
        or gpsservice.DEFAULT_MAP["vin"],
        "odometer": request.POST.get("map_odometer", gpsservice.DEFAULT_MAP["odometer"]).strip()
        or gpsservice.DEFAULT_MAP["odometer"],
        "date": request.POST.get("map_date", gpsservice.DEFAULT_MAP["date"]).strip(),
    }


def _parse_common_date(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@admin_required
@require_http_methods(["GET"])
def import_page(request):
    return render(request, "staff/import.html", {"defaults": gpsservice.DEFAULT_MAP})


@admin_required
@require_http_methods(["GET"])
def import_template(request):
    blob = gpsservice.build_template()
    resp = HttpResponse(
        blob,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="gps_template.xlsx"'
    return resp


@admin_required
@require_http_methods(["POST"])
def import_preview(request):
    upload = request.FILES.get("file")
    mapping = _mapping_from(request)
    common_date = _parse_common_date(request.POST.get("common_date", ""))

    ctx = {"defaults": gpsservice.DEFAULT_MAP, "mapping": mapping,
           "common_date": request.POST.get("common_date", "")}

    if upload is None:
        messages.error(request, "Выберите файл .xlsx.")
        return render(request, "staff/import.html", ctx)
    if not upload.name.lower().endswith(".xlsx"):
        messages.error(request, "Нужен .xlsx. Старый .xls пересохраните в .xlsx и загрузите снова.")
        return render(request, "staff/import.html", ctx)
    if upload.size > gpsservice.MAX_BYTES:
        messages.error(request, "Файл больше 10 МБ.")
        return render(request, "staff/import.html", ctx)

    rel_path = save_import_file(upload.read())
    result = gpsservice.build_preview(rel_path, mapping, common_date, timezone.localdate())
    if result.fatal:
        messages.error(request, result.fatal)
        return render(request, "staff/import.html", ctx)

    ctx.update({
        "result": result,
        "rel_path": rel_path,
        "preview_rows": result.rows[:300],  # показываем первые 300 строк
        "more": max(0, len(result.rows) - 300),
    })
    return render(request, "staff/import.html", ctx)


@admin_required
@require_http_methods(["POST"])
def import_apply(request):
    rel_path = request.POST.get("rel_path", "")
    mapping = _mapping_from(request)
    common_date = _parse_common_date(request.POST.get("common_date", ""))
    if not rel_path:
        messages.error(request, "Потерян файл предпросмотра. Загрузите заново.")
        return redirect("staff_import")
    try:
        batch, result = gpsservice.apply_import(
            rel_path, mapping, common_date, request.user, timezone.localdate()
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("staff_import")

    review.log_action(
        request.user, "import.apply", batch,
        new_value=str(result.counts()),
    )
    messages.success(
        request,
        f"Партия №{batch.id}: принято {result.ready}, на проверке {result.review}, "
        f"дублей {result.duplicate}, ошибок {result.error}.",
    )
    return redirect("staff_import_batches")


@admin_required
@require_http_methods(["GET"])
def import_batches(request):
    batches = list(
        ImportBatch.objects.select_related("author", "reverted_by").order_by("-created_at")
    )
    return render(request, "staff/import_batches.html", {"batches": batches})


@admin_required
@require_http_methods(["GET"])
def import_errors(request, batch_id: int):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    errors = (batch.summary or {}).get("errors", [])
    blob = gpsservice.errors_xlsx(errors)
    resp = HttpResponse(
        blob,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="import_{batch.id}_errors.xlsx"'
    return resp


@admin_required
@require_http_methods(["POST"])
def batch_revert(request, batch_id: int):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Укажите причину отмены партии.")
        return redirect("staff_import_batches")
    try:
        gpsservice.revert_batch(batch, request.user, reason)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("staff_import_batches")
    review.log_action(request.user, "import.revert", batch, reason=reason)
    messages.success(request, f"Партия №{batch.id} отменена. Пробег и ТО пересчитаны.")
    return redirect("staff_import_batches")


# --- Подключение парка из Excel (ТЗ §7) --------------------------------------

def _fleet_mapping_from(request) -> dict:
    return {
        key: (request.POST.get(f"map_{key}", default) or "").strip()
        for key, default in onboardingservice.DEFAULT_MAP.items()
    }


@admin_required
@require_http_methods(["GET"])
def fleet_import_page(request):
    return render(request, "staff/import_fleet.html", {"defaults": onboardingservice.DEFAULT_MAP})


@admin_required
@require_http_methods(["GET"])
def fleet_import_template(request):
    blob = onboardingservice.build_template()
    resp = HttpResponse(
        blob,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="fleet_template.xlsx"'
    return resp


@admin_required
@require_http_methods(["POST"])
def fleet_import_preview(request):
    upload = request.FILES.get("file")
    mapping = _fleet_mapping_from(request)
    ctx = {"defaults": onboardingservice.DEFAULT_MAP, "mapping": mapping}

    if upload is None:
        messages.error(request, "Выберите файл .xlsx.")
        return render(request, "staff/import_fleet.html", ctx)
    if not upload.name.lower().endswith(".xlsx"):
        messages.error(request, "Нужен .xlsx. Старый .xls пересохраните в .xlsx и загрузите снова.")
        return render(request, "staff/import_fleet.html", ctx)
    if upload.size > onboardingservice.MAX_BYTES:
        messages.error(request, "Файл больше 10 МБ.")
        return render(request, "staff/import_fleet.html", ctx)

    rel_path = save_import_file(upload.read())
    result = onboardingservice.build_preview(rel_path, mapping, timezone.localdate())
    if result.fatal:
        messages.error(request, result.fatal)
        return render(request, "staff/import_fleet.html", ctx)

    ctx.update({
        "result": result,
        "rel_path": rel_path,
        "preview_rows": result.rows[:300],
        "more": max(0, len(result.rows) - 300),
    })
    return render(request, "staff/import_fleet.html", ctx)


@admin_required
@require_http_methods(["POST"])
def fleet_import_apply(request):
    rel_path = request.POST.get("rel_path", "")
    mapping = _fleet_mapping_from(request)
    if not rel_path:
        messages.error(request, "Потерян файл предпросмотра. Загрузите заново.")
        return redirect("staff_fleet_import")
    try:
        batch, result = onboardingservice.apply_import(
            rel_path, mapping, request.user, timezone.localdate()
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("staff_fleet_import")

    review.log_action(request.user, "fleet_import.apply", batch, new_value=str(result.counts()))
    messages.success(
        request,
        f"Партия №{batch.id}: подключено {result.ready}, уже было {result.exists}, "
        f"конфликтов {result.conflict}, ошибок {result.error}.",
    )
    return redirect("staff_import_batches")


@staff_required
@require_http_methods(["GET"])
def staff_file(request, attachment_id: int):
    return serve_attachment(request, attachment_id, thumb=False)


@staff_required
@require_http_methods(["GET"])
def staff_file_thumb(request, attachment_id: int):
    return serve_attachment(request, attachment_id, thumb=True)
