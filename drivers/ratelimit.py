"""
Антибрутфорс водительского входа (ТЗ §4): не более 5 неудачных попыток за
15 минут на пару (ИНН+VIN) и на IP. Временная задержка, а не постоянная
блокировка — счётчик истекает вместе с окном. Проверка только на сервере.
"""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.cache import cache


def _key(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"driverlogin:{prefix}:{digest}"


def _client_ip(request) -> str:
    # За обратным прокси реальный адрес — в X-Forwarded-For (первый элемент).
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def is_locked(request, inn: str, vin: str) -> bool:
    """True, если по паре или по IP исчерпан лимит неудачных попыток."""
    limit = settings.DRIVER_LOGIN_MAX_ATTEMPTS
    pair_key = _key("pair", f"{inn}|{vin}")
    ip_key = _key("ip", _client_ip(request))
    return (cache.get(pair_key, 0) >= limit) or (cache.get(ip_key, 0) >= limit)


def register_failure(request, inn: str, vin: str) -> None:
    """Учесть неудачную попытку по паре и по IP в пределах окна."""
    window = settings.DRIVER_LOGIN_WINDOW_SECONDS
    for key in (_key("pair", f"{inn}|{vin}"), _key("ip", _client_ip(request))):
        # add() ставит счётчик с TTL только при первом промахе окна; затем инкремент
        # не продлевает TTL, поэтому окно именно скользит, а не копится бесконечно.
        if cache.add(key, 1, timeout=window) is False:
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=window)


def reset(request, inn: str, vin: str) -> None:
    """Сбросить счётчики после успешного входа."""
    cache.delete(_key("pair", f"{inn}|{vin}"))
    cache.delete(_key("ip", _client_ip(request)))
