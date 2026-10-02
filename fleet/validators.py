"""
Валидация и нормализация идентификаторов входа (ТЗ §4).

Чистые функции без обращения к БД: VIN — 17 символов латиницы и цифр без
I, O, Q; ИНН — строка из 10 или 12 цифр. Нормализация VIN (срез пробелов,
верхний регистр) выполняется до проверки формата.
"""
from __future__ import annotations

import re

from django.core.exceptions import ValidationError

# Допустимые символы VIN: латинские буквы и цифры, исключая I, O, Q.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_INN_RE = re.compile(r"^(\d{10}|\d{12})$")


def normalize_vin(raw: str) -> str:
    """Срезать крайние пробелы и привести к верхнему регистру (ТЗ §4)."""
    return (raw or "").strip().upper()


def normalize_inn(raw: str) -> str:
    """Срезать крайние пробелы; ИНН хранится строкой из цифр."""
    return (raw or "").strip()


def validate_vin(value: str) -> None:
    """Проверить формат VIN. Значение должно быть уже нормализовано."""
    if not _VIN_RE.match(value or ""):
        raise ValidationError(
            "VIN — 17 латинских букв и цифр, без I, O, Q.",
            code="invalid_vin",
        )


def validate_inn(value: str) -> None:
    """Проверить формат ИНН: строка из 10 или 12 цифр."""
    if not _INN_RE.match(value or ""):
        raise ValidationError(
            "ИНН — строка из 10 или 12 цифр.",
            code="invalid_inn",
        )


def is_valid_vin(value: str) -> bool:
    return bool(_VIN_RE.match(value or ""))


def is_valid_inn(value: str) -> bool:
    return bool(_INN_RE.match(value or ""))
