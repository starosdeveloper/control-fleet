"""
Классификация строк массового подключения парка (ТЗ §7) — чистая логика без БД.

ТЗ §7: «Первичное подключение через Excel: VIN, ИНН, менеджер, госномер, модель,
пробег и дата показания, дата и пробег ТО. Импортировать после предварительной
проверки.»

Числа и даты разбираются теми же правилами, что и GPS-импорт (§8): пробелы в
числах допустимы, дробное округляется вниз, формулы и даты в будущем — ошибка
(`gpsimport.normalize_mileage`/`normalize_date`). Здесь эти правила переиспользуются
для необязательной пары «пробег + дата показания» и необязательной пары
«дата ТО + пробег ТО» — в отличие от GPS-строки, где пара обязательна, здесь обе
ячейки пустые — валидное отсутствие пары.

Категории строк:
    READY    — ТС новое или ранее закрыто (нет действующего назначения) — будет
               создано/переоткрыто с этим арендатором и менеджером.
    EXISTS   — ТС уже закреплено за ЭТИМ ЖЕ арендатором — нечего менять, пропуск.
    CONFLICT — ТС уже закреплено за ДРУГИМ арендатором — массовый импорт не
               разрывает действующее назначение; нужна ручная операция в карточке
               ТС (закрыть назначение, затем переподключить).
    ERROR    — неверный формат VIN/ИНН, неизвестный менеджер, неполная пара
               значений, формула/будущая дата/отрицательное, пробег ТО больше
               показания, VIN повторяется в самом файле.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .gpsimport import normalize_date, normalize_mileage


class RowCategory(str, Enum):
    READY = "ready"
    EXISTS = "exists"
    CONFLICT = "conflict"
    ERROR = "error"


def _is_blank(raw) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


@dataclass(frozen=True)
class PairResult:
    mileage: int | None
    when: date | None
    error: str  # непусто → пара ошибочна; обе ячейки пустые — error="" и оба None


def normalize_optional_pair(mileage_raw, date_raw, today: date, label: str) -> PairResult:
    """
    Необязательная пара «пробег + дата» (ТЗ §7). Обе ячейки пустые — пары нет,
    это валидно. Заполнена только одна — ошибка (неполная пара). Обе заполнены —
    проверяются форматом §8 (пробелы, дробное вниз, запрет формул и будущих дат).
    """
    m_blank = _is_blank(mileage_raw)
    d_blank = _is_blank(date_raw)
    if m_blank and d_blank:
        return PairResult(None, None, "")
    if m_blank or d_blank:
        return PairResult(None, None, f"неполная пара «{label}»: заполнено не оба значения")

    mv = normalize_mileage(mileage_raw)
    if mv.error:
        return PairResult(None, None, f"{label}: {mv.error}")
    dv = normalize_date(date_raw, None, today)
    if dv.error:
        return PairResult(None, None, f"{label}: {dv.error}")
    return PairResult(mv.value, dv.value, "")


def cross_check_maint_vs_reading(mileage: int | None, maint_mileage: int | None) -> str:
    """Пробег ТО не может быть больше указанного текущего пробега (как при отправке отчёта, §5)."""
    if mileage is not None and maint_mileage is not None and maint_mileage > mileage:
        return "пробег ТО больше указанного текущего пробега"
    return ""


def classify_onboarding_row(
    vin_ok: bool,
    inn_ok: bool,
    manager_known: bool,
    reading_error: str,
    maint_error: str,
    cross_error: str,
    duplicate_in_file: bool,
    existing_active_tenant_inn: str | None,
    row_inn: str,
) -> tuple[RowCategory, str]:
    """
    Отнести строку к категории. Порядок: формат → менеджер → пары значений →
    повтор VIN в файле → состояние ТС в БД.
    """
    if not vin_ok:
        return RowCategory.ERROR, "неверный формат VIN"
    if not inn_ok:
        return RowCategory.ERROR, "неверный формат ИНН"
    if not manager_known:
        return RowCategory.ERROR, "неизвестный менеджер (нет такого пользователя в группе «Менеджеры»)"
    if reading_error:
        return RowCategory.ERROR, reading_error
    if maint_error:
        return RowCategory.ERROR, maint_error
    if cross_error:
        return RowCategory.ERROR, cross_error
    if duplicate_in_file:
        return RowCategory.ERROR, "VIN повторяется в файле"

    if existing_active_tenant_inn is not None:
        if existing_active_tenant_inn == row_inn:
            return RowCategory.EXISTS, "уже подключено этому арендатору"
        return RowCategory.CONFLICT, (
            f"ТС закреплено за другим арендатором (ИНН {existing_active_tenant_inn}); "
            "массовый импорт не разрывает действующее назначение"
        )

    return RowCategory.READY, ""
