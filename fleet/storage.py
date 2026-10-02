"""
Закрытое файловое хранилище (ТЗ §9).

Файлы лежат вне веб-корня, в PRIVATE_MEDIA_ROOT. Имена генерирует сервер
(uuid), чтобы исключить совпадения и небезопасные пути. Структура каталогов:

    vehicles/<ID_VIN>/reports/<YYYY-MM>/report_<id>/<bucket>/<uuid>.<ext>

где bucket — vehicle | dashboard | defect | maintenance. VIN в пути реальный,
ID ТС неизменяем. Публичных постоянных ссылок нет: раздача только через вью с
проверкой прав (fleet.fileaccess).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings

_BUCKET = {
    "dashboard": "dashboard",
    "vehicle": "vehicle",
    "defect": "defect",
    "work_order": "maintenance",
    "service_book": "maintenance",
}


def _root() -> Path:
    root = Path(settings.PRIVATE_MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def report_rel_dir(report) -> str:
    v = report.vehicle
    return (
        f"vehicles/{v.folder_name()}/reports/"
        f"{report.period_month:%Y-%m}/report_{report.id}"
    )


def _safe_join(rel: str) -> Path:
    """Собрать абсолютный путь внутри хранилища и не дать выйти за его пределы."""
    root = _root().resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Путь вне закрытого хранилища")
    return target


def save_bytes(report, kind: str, ext: str, data: bytes) -> str:
    """
    Записать данные под серверным именем, вернуть относительный путь (для БД).
    ext — без точки (jpg, pdf).
    """
    bucket = _BUCKET.get(kind, "misc")
    rel = f"{report_rel_dir(report)}/{bucket}/{uuid.uuid4().hex}.{ext}"
    target = _safe_join(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel


def thumb_rel_path(rel: str) -> str:
    """Путь миниатюры рядом с оригиналом: <name>.thumb.jpg."""
    p = Path(rel)
    return str(p.with_suffix("")) + ".thumb.jpg"


def save_thumb(rel: str, data: bytes) -> str:
    rel_thumb = thumb_rel_path(rel)
    target = _safe_join(rel_thumb)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel_thumb


def open_abs(rel: str) -> Path:
    """Абсолютный путь существующего файла по относительному (для раздачи)."""
    return _safe_join(rel)


def save_import_file(data: bytes, ext: str = "xlsx") -> str:
    """
    Сохранить исходный файл импорта под серверным именем (ТЗ §8: «Сохранить
    исходный файл»). Возвращает относительный путь.
    """
    rel = f"imports/{uuid.uuid4().hex}.{ext}"
    target = _safe_join(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel


def delete_rel(rel: str) -> None:
    """Удалить файл и его миниатюру, если есть. Молча, если файла нет."""
    for candidate in (rel, thumb_rel_path(rel)):
        try:
            _safe_join(candidate).unlink(missing_ok=True)
        except ValueError:
            pass
