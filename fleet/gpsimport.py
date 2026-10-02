"""
Разбор и классификация строк GPS-импорта (ТЗ §8) — чистая логика без БД и без
чтения файла. На входе уже извлечённые значения ячеек, на выходе — нормализованное
значение и категория строки. Чтение .xlsx и запись в БД — в `fleet.gpsservice`.

Категории строк (ТЗ §8 «Проверка и результат»):
    READY     — корректная, готова к загрузке (принимается);
    REVIEW    — подозрительная (конфликт/откат/скачок) → очередь проверки;
    DUPLICATE — полный дубль VIN+дата+пробег+источник → пропустить;
    ERROR     — неизвестный VIN, пустое/нечисловое/отрицательное, дата в будущем,
                формула → пропустить, строка попадает в Excel ошибок.

Границы §8: пробелы в числах допустимы; дробные км округляются вниз (с отметкой);
разные показания одного VIN на одну дату — конфликт на проверку; полный дубль —
пропуск. Проверка «назад»/«скачок» — через `readings.classify_reading`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from .readings import classify_reading


class RowCategory(str, Enum):
    READY = "ready"
    REVIEW = "review"
    DUPLICATE = "duplicate"
    ERROR = "error"


@dataclass(frozen=True)
class CellValue:
    value: int | None          # нормализованное целое (пробег) или дата как .date
    note: str                  # пометка предпросмотра (напр. «округлено вниз»)
    error: str                 # непусто → ячейка ошибочна


def normalize_mileage(raw) -> CellValue:
    """
    Привести значение пробега к целому км (ТЗ §8).

    Допустимо: int, float, строка с пробелами/неразрывными пробелами, десятичная
    запятая или точка. Дробное округляется вниз с пометкой. Формула (строка с «=»),
    пустое, нечисловое, отрицательное — ошибка.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return CellValue(None, "", "пустое значение пробега")

    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("="):
            return CellValue(None, "", "формула не поддерживается")
        # Пробелы-разделители тысяч (обычные и неразрывные) убираем.
        s = s.replace(" ", "").replace(" ", "")
        s = s.replace(",", ".")
        try:
            num = float(s)
        except ValueError:
            return CellValue(None, "", "нечисловое значение пробега")
    elif isinstance(raw, bool):
        return CellValue(None, "", "нечисловое значение пробега")
    elif isinstance(raw, (int, float)):
        num = float(raw)
    else:
        return CellValue(None, "", "нечисловое значение пробега")

    if math.isnan(num) or math.isinf(num):
        return CellValue(None, "", "нечисловое значение пробега")
    if num < 0:
        return CellValue(None, "", "отрицательный пробег")

    floored = math.floor(num)
    note = "дробное округлено вниз" if floored != num else ""
    return CellValue(int(floored), note, "")


def normalize_date(raw, fallback: date | None, today: date) -> CellValue:
    """
    Привести дату показания к date (ТЗ §8).

    Пустая ячейка допустима, если задана общая дата выгрузки `fallback`. Дата в
    будущем — ошибка. Поддержаны datetime/date и строки «ГГГГ-ММ-ДД», «ДД.ММ.ГГГГ».
    Время, если есть, отбрасывается.
    """
    parsed: date | None = None

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if fallback is None:
            return CellValue(None, "", "нет даты показания")
        parsed = fallback
    elif isinstance(raw, datetime):
        parsed = raw.date()
    elif isinstance(raw, date):
        parsed = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if s.startswith("="):
            return CellValue(None, "", "формула не поддерживается")
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                parsed = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            return CellValue(None, "", "не распознана дата")
    else:
        return CellValue(None, "", "не распознана дата")

    if parsed > today:
        return CellValue(None, "", "дата в будущем")
    return CellValue(parsed, "", "")


def classify_import_row(
    known_vin: bool,
    mileage: CellValue,
    when: CellValue,
    gps_signatures: set[tuple],
    same_date_accepted_values: list[int],
    prior_accepted: list[tuple[int, date]],
    jump_limit: int,
    conflict_limit: int,
) -> tuple[RowCategory, str]:
    """
    Отнести строку к категории (ТЗ §8). Порядок проверок важен: сперва жёсткие
    ошибки, затем дубль, затем конфликт/подозрение.

    gps_signatures — множество (дата, пробег) ранее сохранённых GPS-показаний этого
        VIN (для отсечения полного дубля VIN+дата+пробег+источник).
    same_date_accepted_values — принятые показания этого VIN за ту же дату (любой
        источник) для определения конфликта.
    prior_accepted — ранее принятые (пробег, дата) для проверки «назад»/«скачок».
    """
    if not known_vin:
        return RowCategory.ERROR, "неизвестный VIN"
    if mileage.error:
        return RowCategory.ERROR, mileage.error
    if when.error:
        return RowCategory.ERROR, when.error

    value = mileage.value
    day = when.value

    # Полный дубль VIN+дата+пробег+источник (GPS) — пропустить (ТЗ §8).
    if (day, value) in gps_signatures:
        return RowCategory.DUPLICATE, "дубль VIN+дата+пробег"

    # Разные показания одного VIN на одну дату — конфликт на проверку (ТЗ §8).
    for other in same_date_accepted_values:
        if other != value:
            return RowCategory.REVIEW, (
                f"конфликт за дату: есть принятое {other} км, во вводе {value} км"
            )

    # «Назад»/«скачок» относительно ранее принятых (ТЗ §6).
    verdict = classify_reading(
        new_value=value,
        new_date=day,
        prior_accepted=prior_accepted,
        same_date_other_source=[],
        daily_jump_limit=jump_limit,
        conflict_limit=conflict_limit,
    )
    if not verdict.accepted:
        return RowCategory.REVIEW, verdict.reason

    return RowCategory.READY, mileage.note
