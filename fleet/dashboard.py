"""
Сборка кабинета сотрудника (ТЗ §7): дашборд месяца, таблица контроля, срез на
дату. Здесь — доступы к БД; смысл (срез, расчёт ТО, сравнение) берётся из чистых
крейтов `asof`, `maintenance`, `metrics`, которые проверены на хосте.

Область видимости — через `access.vehicles_for_staff`: менеджер видит только свой
парк, администратор — весь, с возможностью отфильтровать по менеджеру.

Дата среза (ТЗ §7):
    завершённый месяц → последний день месяца, состояние считается на него;
    текущий месяц      → сегодня, состояние на момент просмотра.
Историческую карточку по сегодняшнему пробегу не пересчитывать: учитываются
только показания и ТО с датой не позже среза.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.db.models import Prefetch

from . import metrics
from .access import is_admin, vehicles_for_staff
from .asof import accepted_reading_asof, confirmed_maint_asof
from .maintenance import MaintStatus, MaintState, maintenance_state
from .models import (
    Assignment,
    ImportBatch,
    Maintenance,
    OdometerReading,
    Report,
    Vehicle,
)


# --- Месяц и срез -------------------------------------------------------------

def parse_month(raw: str | None, today: date) -> date:
    """
    Разобрать «YYYY-MM» в первое число месяца. Пустое/битое → текущий месяц.
    """
    if raw:
        try:
            year, month = raw.split("-")
            return date(int(year), int(month), 1)
        except (ValueError, TypeError):
            pass
    return today.replace(day=1)


def month_bounds(month_first: date) -> tuple[date, date]:
    """Первый и последний день месяца, которому принадлежит month_first."""
    last_day = calendar.monthrange(month_first.year, month_first.month)[1]
    return month_first, month_first.replace(day=last_day)


def cut_for_month(month_first: date, today: date) -> tuple[date, bool]:
    """
    Дата среза и признак завершённости месяца.

    Завершённый прошлый месяц → срез на последний день месяца, complete=True.
    Текущий месяц → срез на сегодня, complete=False.
    Будущий месяц (не штатно) → срез на сегодня, complete=False.
    """
    first, last = month_bounds(month_first)
    if last < today:
        return last, True
    if first > today:
        return today, False
    return today, False


def months_back(month_first: date, n: int) -> date:
    """Первое число месяца на n месяцев раньше."""
    year = month_first.year
    month = month_first.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


# --- Строка таблицы контроля --------------------------------------------------

@dataclass
class DashboardRow:
    vehicle: Vehicle
    tenant_inn: str
    manager_name: str
    last_report_date: date | None          # дата последнего отправленного отчёта
    mileage_km: int | None                 # принятый пробег на срез
    mileage_date: date | None
    mileage_source: str                    # код источника ('driver'/'gps'/'manual') или ''
    maint_date: date | None                # дата базового подтверждённого ТО
    maint_mileage_km: int | None           # пробег базового ТО
    maint: MaintState                       # статус/цвет/остаток/порог
    reported_in_month: bool                # полный отчёт за месяц отправлен
    photos_in_month: bool                  # 4 обязательных ракурса в отчёте месяца
    has_comment: bool                      # есть замечание/комментарий в отчёте месяца
    pending_reading: bool                  # есть показание на проверке
    pending_maint: bool                    # есть ТО на проверке


@dataclass
class Indicators:
    """Числа дашборда за месяц (ТЗ §7)."""
    fleet_total: int = 0
    submitted: int = 0
    photos: int = 0
    overdue: int = 0            # остаток < 0
    due: int = 0               # остаток = 0
    no_maint_base: int = 0     # нет подтверждённой базы ТО
    not_submitted: int = 0
    no_current_mileage: int = 0  # ТС без актуального принятого пробега на срез


@dataclass
class Dashboard:
    month_first: date
    cut: date
    complete: bool
    indicators: Indicators
    comparison: metrics.MonthComparison
    rows: list[DashboardRow] = field(default_factory=list)
    manager_id: int | None = None


# --- Выбор отчётного парка ----------------------------------------------------

def reporting_fleet(user, month_first: date, manager_id: int | None) -> "list[Vehicle]":
    """
    ТС отчётного парка месяца (ТЗ §7): уникальные VIN, имевшие активное назначение
    хотя бы один день выбранного месяца, в пределах видимости сотрудника.

    manager_id (только для администратора) ограничивает выборку текущим парком
    указанного менеджера. Для менеджера область уже ограничена им самим.
    """
    from django.db.models import Q

    first, last = month_bounds(month_first)
    base = vehicles_for_staff(user)

    if is_admin(user) and manager_id:
        base = base.filter(
            assignments__ended_at__isnull=True,
            assignments__manager_assignments__ended_at__isnull=True,
            assignments__manager_assignments__manager_id=manager_id,
        ).distinct()

    # Назначение пересекает месяц: началось не позже конца месяца и либо ещё не
    # закрыто, либо закрыто не раньше начала месяца. Оба условия — в ОДНОМ filter()
    # на связь `assignments`, иначе Django может удовлетворить их разными строками
    # (одно назначение началось, другое открыто) и раздуть парк.
    overlap = Q(assignments__started_at__date__lte=last) & (
        Q(assignments__ended_at__isnull=True)
        | Q(assignments__ended_at__date__gte=first)
    )
    return list(base.filter(overlap).distinct().order_by("id"))


# --- Сборка строки ------------------------------------------------------------

def _assignment_for_month(vehicle: Vehicle, first: date, last: date) -> Assignment | None:
    """Назначение, актуальное для месяца: активное на конец месяца, иначе последнее пересекающее."""
    overlapping = [
        a
        for a in vehicle.assignments.all()
        if a.started_at.date() <= last
        and (a.ended_at is None or a.ended_at.date() >= first)
    ]
    if not overlapping:
        return None
    # Предпочесть действующее на конец месяца; иначе — начавшееся позже всех.
    active = [a for a in overlapping if a.ended_at is None or a.ended_at.date() > last]
    pool = active or overlapping
    return max(pool, key=lambda a: a.started_at)


def _build_row(
    vehicle: Vehicle,
    first: date,
    last: date,
    cut: date,
    warning_km: int,
) -> DashboardRow:
    assignment = _assignment_for_month(vehicle, first, last)
    tenant_inn = assignment.tenant.inn if assignment else ""
    manager = assignment.manager if assignment else None
    manager_name = manager.get_username() if manager else ""

    # Принятые показания на срез (любой источник), без отменённых партий (ТЗ §8).
    accepted = [
        (r.value_km, r.measured_at, r.source)
        for r in vehicle.readings.all()
        if r.verification == OdometerReading.Verification.ACCEPTED
        and not (
            r.import_batch_id
            and r.import_batch
            and r.import_batch.status == ImportBatch.Status.REVERTED
        )
    ]
    chosen_reading = accepted_reading_asof(accepted, cut)
    if chosen_reading:
        mileage_km, mileage_date, mileage_source = chosen_reading
    else:
        mileage_km = mileage_date = None
        mileage_source = ""

    # База ТО на срез (только подтверждённые).
    confirmed = [
        (m.mileage_km, m.performed_on)
        for m in vehicle.maintenances.all()
        if m.status == Maintenance.Status.CONFIRMED
    ]
    chosen_maint = confirmed_maint_asof(confirmed, cut)
    if chosen_maint:
        maint_mileage_km, maint_date = chosen_maint
    else:
        maint_mileage_km = maint_date = None

    maint = maintenance_state(
        confirmed_maint_mileage=maint_mileage_km,
        current_mileage=mileage_km,
        interval_km=vehicle.maintenance_interval_km,
        warning_km=warning_km,
    )

    # Отчёт месяца и последний отчёт.
    month_reports = [
        r
        for r in vehicle.reports.all()
        if r.state == Report.State.SUBMITTED and r.period_month == first
    ]
    reported_in_month = bool(month_reports)
    photos_in_month = any(_has_four_views(r) for r in month_reports)
    has_comment = any(
        r.defects == Report.Defects.HAS or r.comment.strip() for r in month_reports
    )

    submitted_reports = [
        r for r in vehicle.reports.all() if r.state == Report.State.SUBMITTED
    ]
    last_report_date = (
        max(r.submitted_at for r in submitted_reports).date()
        if submitted_reports
        else None
    )

    pending_reading = any(
        r.verification == OdometerReading.Verification.PENDING
        for r in vehicle.readings.all()
    )
    pending_maint = any(
        m.status == Maintenance.Status.REPORTED for m in vehicle.maintenances.all()
    )

    return DashboardRow(
        vehicle=vehicle,
        tenant_inn=tenant_inn,
        manager_name=manager_name,
        last_report_date=last_report_date,
        mileage_km=mileage_km,
        mileage_date=mileage_date,
        mileage_source=mileage_source,
        maint_date=maint_date,
        maint_mileage_km=maint_mileage_km,
        maint=maint,
        reported_in_month=reported_in_month,
        photos_in_month=photos_in_month,
        has_comment=has_comment,
        pending_reading=pending_reading,
        pending_maint=pending_maint,
    )


def _has_four_views(report: Report) -> bool:
    """Все четыре обязательных ракурса приложены к отчёту (ТЗ §5, §7)."""
    from .models import Attachment

    required = {
        Attachment.View.FRONT,
        Attachment.View.BACK,
        Attachment.View.LEFT,
        Attachment.View.RIGHT,
    }
    present = {
        a.view
        for a in report.attachments.all()
        if a.kind == Attachment.Kind.VEHICLE
    }
    return required.issubset(present)


# --- Фильтры таблицы ----------------------------------------------------------

def _passes_filters(row: DashboardRow, f: dict) -> bool:
    """Применить фильтры таблицы контроля (ТЗ §7) к собранной строке."""
    q = (f.get("q") or "").strip().upper()
    if q and q not in row.vehicle.vin.upper() and q not in (row.tenant_inn or ""):
        return False

    flag = f.get("flag") or ""
    if flag == "no_report" and row.reported_in_month:
        return False
    if flag == "overdue" and row.maint.status != MaintStatus.OVERDUE:
        return False
    if flag == "due" and row.maint.status != MaintStatus.DUE:
        return False
    if flag == "no_base" and row.maint.status != MaintStatus.NO_DATA:
        return False
    if flag == "pending_reading" and not row.pending_reading:
        return False
    if flag == "pending_maint" and not row.pending_maint:
        return False
    return True


# --- Главная сборка -----------------------------------------------------------

def build_dashboard(
    user,
    month_raw: str | None,
    manager_id: int | None,
    filters: dict | None,
    today: date,
) -> Dashboard:
    """
    Собрать дашборд и таблицу контроля за месяц для сотрудника.

    filters — словарь таблицы: {'q': str, 'flag': str}. Фильтры применяются к уже
    собранным строкам, потому что статус ТО и признак отчёта вычисляются на срез.
    """
    filters = filters or {}
    warning_km = settings.MAINTENANCE_WARNING_KM
    month_first = parse_month(month_raw, today)
    first, last = month_bounds(month_first)
    cut, complete = cut_for_month(month_first, today)

    vehicles = _prefetched(reporting_fleet(user, month_first, manager_id))

    all_rows = [_build_row(v, first, last, cut, warning_km) for v in vehicles]

    indicators = _count(all_rows)

    # Сравнение с прошлым месяцем: та же область видимости, свой отчётный парк.
    prior_first = months_back(month_first, 1)
    prior_vehicles = _prefetched(reporting_fleet(user, prior_first, manager_id))
    pfirst, plast = month_bounds(prior_first)
    pcut, _pcomplete = cut_for_month(prior_first, today)
    prior_rows = [_build_row(v, pfirst, plast, pcut, warning_km) for v in prior_vehicles]
    prior_submitted = sum(1 for r in prior_rows if r.reported_in_month)

    comparison = metrics.compare_months(
        current_submitted=indicators.submitted,
        current_total=indicators.fleet_total,
        prior_submitted=prior_submitted,
        prior_total=len(prior_rows),
        current_month_complete=complete,
    )

    rows = [r for r in all_rows if _passes_filters(r, filters)]

    return Dashboard(
        month_first=month_first,
        cut=cut,
        complete=complete,
        indicators=indicators,
        comparison=comparison,
        rows=rows,
        manager_id=manager_id,
    )


def _count(rows: list[DashboardRow]) -> Indicators:
    ind = Indicators(fleet_total=len(rows))
    for r in rows:
        if r.reported_in_month:
            ind.submitted += 1
        if r.photos_in_month:
            ind.photos += 1
        status = r.maint.status
        if status == MaintStatus.OVERDUE:
            ind.overdue += 1
        elif status == MaintStatus.DUE:
            ind.due += 1
        elif status == MaintStatus.NO_DATA:
            # Нет базы ТО (серый), либо база есть, но нет актуального пробега.
            if r.maint_mileage_km is None:
                ind.no_maint_base += 1
        if r.mileage_km is None:
            ind.no_current_mileage += 1
    ind.not_submitted = ind.fleet_total - ind.submitted
    return ind


def _prefetched(vehicles: list[Vehicle]) -> list[Vehicle]:
    """Перечитать список ТС с подгрузкой связей, чтобы строки не делали N+1 запросов."""
    if not vehicles:
        return []
    ids = [v.id for v in vehicles]
    return list(
        Vehicle.objects.filter(id__in=ids)
        .prefetch_related(
            Prefetch(
                "assignments",
                queryset=Assignment.objects.select_related("tenant").prefetch_related(
                    "manager_assignments__manager"
                ),
            ),
            Prefetch(
                "readings",
                queryset=OdometerReading.objects.select_related("import_batch"),
            ),
            "maintenances",
            Prefetch("reports", queryset=Report.objects.prefetch_related("attachments")),
        )
        .order_by("id")
    )
