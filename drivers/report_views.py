"""
Четыре экрана отправки месячного отчёта (ТЗ §5).

Один черновик на действующее назначение. Каждый экран сохраняет введённое
(кнопки «Назад»/«Далее» не теряют данные). Полнота проверяется только перед
отправкой; просрочка ТО отправку не блокирует. Отправка идемпотентна.
"""
from __future__ import annotations

import uuid
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from fleet import attachments
from fleet.attachments import AttachmentError
from fleet.maintenance import maintenance_state
from fleet.models import Attachment, Maintenance, OdometerReading, Report
from fleet.submit import REQUIRED_VIEWS, completeness_errors, submit_report

from .session import driver_required

_VIEW_LABELS = {
    Attachment.View.FRONT: "Спереди",
    Attachment.View.BACK: "Сзади",
    Attachment.View.LEFT: "Слева",
    Attachment.View.RIGHT: "Справа",
}


def _get_or_create_draft(assignment) -> Report:
    draft = assignment.reports.filter(state=Report.State.DRAFT).first()
    if draft is not None:
        return draft
    return Report.objects.create(
        vehicle=assignment.vehicle,
        assignment=assignment,
        period_month=timezone.localdate().replace(day=1),
        state=Report.State.DRAFT,
        idempotency_key=uuid.uuid4().hex,
        current_step=1,
    )


def _slot_map(draft: Report) -> dict:
    """Текущие вложения по слотам для отрисовки миниатюр."""
    atts = list(draft.attachments.all())
    dashboard = next((a for a in atts if a.kind == Attachment.Kind.DASHBOARD), None)
    vehicle = {a.view: a for a in atts if a.kind == Attachment.Kind.VEHICLE}
    defects = [a for a in atts if a.kind == Attachment.Kind.DEFECT]
    work_orders = [a for a in atts if a.kind == Attachment.Kind.WORK_ORDER]
    service_book = [a for a in atts if a.kind == Attachment.Kind.SERVICE_BOOK]
    return {
        "dashboard": dashboard,
        "vehicle": vehicle,
        "defects": defects,
        "work_orders": work_orders,
        "service_book": service_book,
    }


def _parse_int(raw: str) -> int | None:
    raw = (raw or "").replace(" ", "").strip()
    if raw == "":
        return None
    if not raw.isdigit():
        raise ValueError
    return int(raw)


@driver_required
@require_http_methods(["GET"])
def report_start(request):
    draft = _get_or_create_draft(request.driver_assignment)
    step = draft.current_step if draft.current_step in (1, 2, 3) else 1
    return redirect(f"driver_report_step{step}")


@driver_required
@require_http_methods(["GET", "POST"])
def report_step1(request):
    """Экран 1: фото одометра, показание, блок сведений о ТО."""
    draft = _get_or_create_draft(request.driver_assignment)
    field_errors: dict[str, str] = {}

    if request.method == "POST":
        action = request.POST.get("action", "")

        delete_id = request.POST.get("delete_id")
        if delete_id:
            attachments.remove(draft, int(delete_id))
            return redirect("driver_report_step1")

        # Показание одометра.
        try:
            draft.odometer_value_km = _parse_int(request.POST.get("odometer_value_km", ""))
        except ValueError:
            field_errors["odometer"] = "Введите целое неотрицательное число."

        # Сведения о ТО.
        choice = request.POST.get("maint_choice", "")
        if choice in Report.MaintChoice.values:
            draft.maint_choice = choice
        if draft.maint_choice == Report.MaintChoice.DONE:
            try:
                draft.maint_mileage_km = _parse_int(request.POST.get("maint_mileage_km", ""))
            except ValueError:
                field_errors["maint_mileage"] = "Введите целое число."
            raw_date = request.POST.get("maint_date", "").strip()
            if raw_date:
                try:
                    draft.maint_date = date.fromisoformat(raw_date)
                except ValueError:
                    field_errors["maint_date"] = "Дата в формате ГГГГ-ММ-ДД."
            else:
                draft.maint_date = None
            draft.maint_work_order = request.POST.get("maint_work_order", "").strip()[:120]
            draft.maint_no_document = bool(request.POST.get("maint_no_document"))
            draft.maint_reason = request.POST.get("maint_reason", "").strip()
        else:
            draft.maint_date = None
            draft.maint_mileage_km = None

        # Загрузка фото одометра и документов ТО.
        _handle_uploads(request, draft, {
            "photo_dashboard": (Attachment.Kind.DASHBOARD, Attachment.View.NONE),
            "photo_work_order": (Attachment.Kind.WORK_ORDER, Attachment.View.NONE),
            "photo_service_book": (Attachment.Kind.SERVICE_BOOK, Attachment.View.NONE),
        })
        draft.save()

        if field_errors:
            pass  # перерисовать с ошибками у полей
        elif action == "next":
            draft.current_step = max(draft.current_step, 2)
            draft.save(update_fields=["current_step"])
            return redirect("driver_report_step2")
        else:
            return redirect("driver_report_step1")  # PRG после загрузки/сохранения

    # Живой расчёт состояния ТО по введённому показанию (ТЗ §5).
    maint = _live_maint_state(draft)
    context = {
        "draft": draft,
        "vehicle": draft.vehicle,
        "slots": _slot_map(draft),
        "maint": maint,
        "field_errors": field_errors,
        "MaintChoice": Report.MaintChoice,
        "today": timezone.localdate().isoformat(),
    }
    return render(request, "drivers/report_step1.html", context)


@driver_required
@require_http_methods(["GET", "POST"])
def report_step2(request):
    """Экран 2: четыре обязательных ракурса + до пяти фото дефектов."""
    draft = _get_or_create_draft(request.driver_assignment)

    if request.method == "POST":
        action = request.POST.get("action", "")
        delete_id = request.POST.get("delete_id")
        if delete_id:
            attachments.remove(draft, int(delete_id))
            return redirect("driver_report_step2")
        if action == "back":
            return redirect("driver_report_step1")

        uploads = {
            f"photo_{v.value}": (Attachment.Kind.VEHICLE, v) for v in REQUIRED_VIEWS
        }
        uploads["photo_defect"] = (Attachment.Kind.DEFECT, Attachment.View.NONE)
        _handle_uploads(request, draft, uploads)

        if action == "next":
            draft.current_step = max(draft.current_step, 3)
            draft.save(update_fields=["current_step"])
            return redirect("driver_report_step3")
        return redirect("driver_report_step2")  # PRG после загрузки

    slots = _slot_map(draft)
    view_slots = [
        (v.value, _VIEW_LABELS[v], slots["vehicle"].get(v.value)) for v in REQUIRED_VIEWS
    ]
    context = {
        "draft": draft,
        "vehicle": draft.vehicle,
        "slots": slots,
        "view_slots": view_slots,
        "max_defects": attachments.MAX_DEFECTS,
    }
    return render(request, "drivers/report_step2.html", context)


@driver_required
@require_http_methods(["GET", "POST"])
def report_step3(request):
    """Экран 3: замечания, комментарий, краткая проверка и отправка."""
    draft = _get_or_create_draft(request.driver_assignment)

    if request.method == "POST":
        defects = request.POST.get("defects", "")
        if defects in Report.Defects.values:
            draft.defects = defects
        draft.comment = request.POST.get("comment", "").strip()[:2000]
        draft.save(update_fields=["defects", "comment"])

        action = request.POST.get("action")
        if action == "back":
            return redirect("driver_report_step2")
        if action == "submit":
            errors = completeness_errors(draft)
            if errors:
                for e in errors:
                    messages.error(request, e)
            else:
                report = submit_report(draft)
                request.session["last_report_id"] = report.id
                return redirect("driver_report_done")

    errors = completeness_errors(draft)
    context = {
        "draft": draft,
        "vehicle": draft.vehicle,
        "slots": _slot_map(draft),
        "check_errors": errors,
        "Defects": Report.Defects,
    }
    return render(request, "drivers/report_step3.html", context)


@driver_required
@require_http_methods(["GET"])
def report_done(request):
    """Экран 4: подтверждение. Показывается только после успешного сохранения."""
    report_id = request.session.get("last_report_id")
    report = None
    if report_id:
        report = (
            Report.objects.filter(
                id=report_id,
                assignment=request.driver_assignment,
                state=Report.State.SUBMITTED,
            ).first()
        )
    if report is None:
        return redirect("driver_home")
    return render(request, "drivers/report_done.html", {"report": report})


def _handle_uploads(request, draft: Report, mapping: dict) -> None:
    for field, (kind, view) in mapping.items():
        uploaded = request.FILES.get(field)
        if uploaded is None:
            continue
        view_value = view.value if hasattr(view, "value") else view
        try:
            attachments.attach(draft, kind, view_value, uploaded)
        except AttachmentError as exc:
            messages.error(request, f"Фото не принято: {exc}")


def _live_maint_state(draft: Report):
    """Состояние ТО по введённому в черновик показанию (для подсказки на экране 1)."""
    vehicle = draft.vehicle
    confirmed = (
        Maintenance.objects.filter(vehicle=vehicle, status=Maintenance.Status.CONFIRMED)
        .order_by("-mileage_km")
        .first()
    )
    accepted = (
        OdometerReading.objects.filter(
            vehicle=vehicle, verification=OdometerReading.Verification.ACCEPTED
        )
        .order_by("-measured_at", "-received_at")
        .first()
    )
    current = draft.odometer_value_km
    if current is None and accepted is not None:
        current = accepted.value_km
    return maintenance_state(
        confirmed_maint_mileage=confirmed.mileage_km if confirmed else None,
        current_mileage=current,
        interval_km=vehicle.maintenance_interval_km,
        warning_km=settings.MAINTENANCE_WARNING_KM,
    )
