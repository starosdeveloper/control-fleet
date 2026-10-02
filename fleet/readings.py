"""
Классификация показаний одометра (ТЗ §6): принять или направить на проверку.

Чистая логика отдельно от БД. На входе — новое показание и ранее принятые
записи; на выходе — статус проверки и причина. Границы §6:

  • показание меньше ранее принятого при не более поздней дате → на проверку
    (одометр «поехал назад»);
  • скачок более daily_jump_limit км в сутки относительно ближайшей ранней
    принятой записи → на проверку (порог настраивается);
  • расхождение с показанием другого источника за ту же дату больше
    conflict_limit км → конфликт, на проверку.

Если ни одно условие не сработало — принять. Отчёт и фото в любом случае
сохраняются вызывающей стороной; актуальный пробег автоматически не заменяется,
пока запись на проверке.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ReadingVerdict:
    accepted: bool
    reason: str  # пусто, если принято


def classify_reading(
    new_value: int,
    new_date: date,
    prior_accepted: list[tuple[int, date]],
    same_date_other_source: list[int],
    daily_jump_limit: int,
    conflict_limit: int,
) -> ReadingVerdict:
    """
    prior_accepted — список (значение, дата измерения) ранее ПРИНЯТЫХ показаний
        (любой источник), не обязательно отсортированный.
    same_date_other_source — значения принятых записей другого источника за ту
        же дату измерения, что и новое показание.
    """
    # Конфликт источников за одну дату (ТЗ §6: GPS vs водитель > 1000 км).
    for other in same_date_other_source:
        if abs(other - new_value) > conflict_limit:
            return ReadingVerdict(
                False,
                f"Конфликт за одну дату: расхождение {abs(other - new_value)} км "
                f"(> {conflict_limit}).",
            )

    # Одометр назад: есть принятое показание не позже новой даты, но больше по значению.
    for value, when in prior_accepted:
        if when <= new_date and value > new_value:
            return ReadingVerdict(
                False,
                f"Показание {new_value} км меньше ранее принятого {value} км "
                f"от {when:%d.%m.%Y}.",
            )

    # Скачок за сутки относительно ближайшей ранней принятой записи.
    earlier = [(v, d) for (v, d) in prior_accepted if d < new_date]
    if earlier:
        prev_value, prev_date = max(earlier, key=lambda vd: vd[1])
        days = (new_date - prev_date).days
        if days >= 1:
            growth = new_value - prev_value
            if growth > daily_jump_limit * days:
                return ReadingVerdict(
                    False,
                    f"Скачок {growth} км за {days} сут "
                    f"(> {daily_jump_limit} км/сут).",
                )

    return ReadingVerdict(True, "")
