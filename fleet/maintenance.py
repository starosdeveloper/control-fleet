"""
Расчёт состояния ТО (ТЗ §6) — чистая логика без обращения к БД и MMIO-подобных
доступов. Крейт-«смысл»: на входе база подтверждённого ТО, интервал и актуальный
пробег, на выходе — статус, цвет и текст. Проверяется на хосте против спецификации,
а не против одной машины.

Основание расчёта (ТЗ §6):
    следующее ТО = пробег подтверждённого ТО + интервал
    остаток       = следующее ТО − актуальный принятый пробег
Не отсчитывать интервал от нуля, даты отчёта или первого случайного показания.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MaintStatus(str, Enum):
    NO_DATA = "no_data"      # нет исходного подтверждённого ТО
    OK = "ok"                # остаток больше порога предупреждения
    SOON = "soon"            # остаток в пределах порога
    DUE = "due"              # остаток равен нулю — интервал достигнут
    OVERDUE = "overdue"      # остаток меньше нуля — просрочено


class Color(str, Enum):
    GRAY = "gray"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class MaintState:
    status: MaintStatus
    color: Color
    text: str
    next_due_km: int | None      # порог следующего ТО, None если нет базы
    remainder_km: int | None     # остаток до следующего ТО, None если нет базы


def maintenance_state(
    confirmed_maint_mileage: int | None,
    current_mileage: int | None,
    interval_km: int,
    warning_km: int,
) -> MaintState:
    """
    Вернуть состояние ТО по правилам ТЗ §6.

    confirmed_maint_mileage — пробег последнего ПОДТВЕРЖДЁННОГО ТО (база); None,
        если подтверждённой базы ещё нет.
    current_mileage — актуальный принятый пробег; None трактуется как отсутствие
        принятого показания (нечего считать).
    interval_km — интервал ТО данного ТС.
    warning_km — порог жёлтой зоны (ТЗ: 5 000 км).

    Границы (ТЗ §6):
        остаток > warning           → зелёный «До ТО осталось … км»
        1 ≤ остаток ≤ warning       → жёлтый  «Скоро ТО. Осталось … км»
        остаток = 0                 → красный «ТО требуется сейчас»
        остаток < 0                 → красный «ТО просрочено на … км»
        нет базы ТО                 → серый   «Нет данных о последнем ТО»
    """
    if confirmed_maint_mileage is None:
        return MaintState(
            status=MaintStatus.NO_DATA,
            color=Color.GRAY,
            text="Нет данных о последнем ТО",
            next_due_km=None,
            remainder_km=None,
        )

    next_due = confirmed_maint_mileage + interval_km

    if current_mileage is None:
        # База есть, но принятого пробега нет: остаток неизвестен, в просрочку не относим.
        return MaintState(
            status=MaintStatus.NO_DATA,
            color=Color.GRAY,
            text="Нет данных о текущем пробеге",
            next_due_km=next_due,
            remainder_km=None,
        )

    remainder = next_due - current_mileage

    if remainder > warning_km:
        return MaintState(
            MaintStatus.OK, Color.GREEN,
            f"До ТО осталось {remainder} км", next_due, remainder,
        )
    if remainder >= 1:
        return MaintState(
            MaintStatus.SOON, Color.YELLOW,
            f"Скоро ТО. Осталось {remainder} км", next_due, remainder,
        )
    if remainder == 0:
        return MaintState(
            MaintStatus.DUE, Color.RED,
            "ТО требуется сейчас", next_due, remainder,
        )
    return MaintState(
        MaintStatus.OVERDUE, Color.RED,
        f"ТО просрочено на {-remainder} км", next_due, remainder,
    )
