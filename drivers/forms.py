"""Форма входа водителя (ТЗ §4): два поля — ИНН арендатора и VIN."""
from __future__ import annotations

from django import forms

from fleet.validators import (
    normalize_inn,
    normalize_vin,
    validate_inn,
    validate_vin,
)


class DriverLoginForm(forms.Form):
    inn = forms.CharField(
        label="ИНН арендатора",
        max_length=12,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "off", "autofocus": True}
        ),
    )
    vin = forms.CharField(
        label="VIN транспортного средства",
        max_length=17,
        widget=forms.TextInput(
            attrs={"autocapitalize": "characters", "autocomplete": "off"}
        ),
    )

    def clean_inn(self) -> str:
        value = normalize_inn(self.cleaned_data["inn"])
        validate_inn(value)  # несоответствие формату показываем до отправки на проверку пары
        return value

    def clean_vin(self) -> str:
        value = normalize_vin(self.cleaned_data["vin"])
        validate_vin(value)
        return value
