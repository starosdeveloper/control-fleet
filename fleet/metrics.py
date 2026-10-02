"""
Сравнение месячных показателей (ТЗ §7) — чистая логика без обращения к БД.

«Сравнение: числовая разница и доля выполнения относительно парка каждого месяца.
Если в прошлом месяце база равна нулю, процент изменения не рассчитывать. Для
неполного текущего месяца написать „Месяц не завершён“.»

Доля выполнения считается относительно парка СВОЕГО месяца (у каждого месяца свой
знаменатель). Процент изменения — отношение прироста поданных отчётов к прошлому
месяцу; при нулевой базе прошлого месяца он не определён и не выводится.
"""
from __future__ import annotations

from dataclasses import dataclass


def completion_share(submitted: int, fleet_total: int) -> float | None:
    """Доля выполнения за месяц, %. None, если в парке нет ни одного ТС (деление на нуль)."""
    if fleet_total <= 0:
        return None
    return submitted / fleet_total * 100.0


@dataclass(frozen=True)
class MonthComparison:
    current_submitted: int
    current_total: int
    prior_submitted: int
    prior_total: int
    current_share: float | None   # доля выполнения текущего месяца, %
    prior_share: float | None     # доля выполнения прошлого месяца, %
    diff_submitted: int           # разница числа поданных: текущий − прошлый
    pct_change: float | None      # % изменения поданных; None при нулевой базе
    note: str                     # «Месяц не завершён» для неполного текущего, иначе пусто


def compare_months(
    current_submitted: int,
    current_total: int,
    prior_submitted: int,
    prior_total: int,
    current_month_complete: bool,
) -> MonthComparison:
    """
    Сравнить текущий месяц с прошлым (ТЗ §7).

    current_month_complete — завершён ли календарно текущий месяц. Для неполного
        в note пишется «Месяц не завершён» (числа всё равно считаются — они
        промежуточные, но честные на момент просмотра).
    """
    diff = current_submitted - prior_submitted

    # Процент изменения не определён, если в прошлом месяце не подавали ничего.
    pct_change: float | None
    if prior_submitted == 0:
        pct_change = None
    else:
        pct_change = (current_submitted - prior_submitted) / prior_submitted * 100.0

    note = "" if current_month_complete else "Месяц не завершён"

    return MonthComparison(
        current_submitted=current_submitted,
        current_total=current_total,
        prior_submitted=prior_submitted,
        prior_total=prior_total,
        current_share=completion_share(current_submitted, current_total),
        prior_share=completion_share(prior_submitted, prior_total),
        diff_submitted=diff,
        pct_change=pct_change,
        note=note,
    )
