"""
Раздача вложений из закрытого хранилища (ТЗ §9).

Публичных постоянных ссылок нет. На каждый запрос проверяется право на VIN:
водительская сессия — только свой VIN; сотрудник — через can_staff_access_vehicle.
Нет права — 404 (не раскрываем существование файла). Содержимое не исполняется:
отдаём как вложение с нейтральным типом и Content-Disposition.

На рабочем сервере статику отдаёт веб-сервер, но закрытые файлы всегда идут
через эту проверку (X-Accel-Redirect/X-Sendfile можно добавить при развёртывании).
"""
from __future__ import annotations

from django.http import FileResponse, Http404

from .access import can_staff_access_vehicle
from .models import Attachment
from .storage import open_abs, thumb_rel_path


def _may_access(request, vehicle) -> bool:
    if vehicle is None:
        return False
    driver_assignment = getattr(request, "driver_assignment", None)
    if driver_assignment is not None and driver_assignment.vehicle_id == vehicle.id:
        return True
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return can_staff_access_vehicle(user, vehicle)
    return False


def serve_attachment(request, attachment_id: int, thumb: bool = False) -> FileResponse:
    attachment = (
        Attachment.objects.select_related("report__vehicle", "maintenance__vehicle")
        .filter(pk=attachment_id)
        .first()
    )
    if attachment is None or not _may_access(request, attachment.vehicle):
        raise Http404

    rel = thumb_rel_path(attachment.stored_path) if thumb else attachment.stored_path
    path = open_abs(rel)
    if not path.exists():
        # Миниатюры у PDF нет — отдадим оригинал как запасной вариант.
        if thumb:
            path = open_abs(attachment.stored_path)
        if not path.exists():
            raise Http404

    content_type = "image/jpeg" if thumb else (attachment.content_type or "application/octet-stream")
    resp = FileResponse(path.open("rb"), content_type=content_type)
    resp["Content-Disposition"] = f'inline; filename="{path.name}"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp
