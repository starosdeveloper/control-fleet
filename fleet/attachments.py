"""
Приём вложений отчёта (ТЗ §5, §9).

Фото одометра и четыре ракурса ТС — по одному слоту: новый файл заменяет
прежний (фото можно заменить до отправки). Дефекты, заказ-наряды и страницы
сервисной книжки — множественные, с ограничением сверху. Файлы проходят проверку
реального формата и обработку перед сохранением в закрытое хранилище.
"""
from __future__ import annotations

from django.db import transaction

from . import images, storage
from .models import Attachment

# Пределы множественных вложений (ТЗ §5, §9).
MAX_DEFECTS = 5
MAX_WORK_ORDER = 5
MAX_SERVICE_BOOK = 5

_SINGLE_SLOT_KINDS = {Attachment.Kind.DASHBOARD, Attachment.Kind.VEHICLE}
_MULTI_LIMITS = {
    Attachment.Kind.DEFECT: MAX_DEFECTS,
    Attachment.Kind.WORK_ORDER: MAX_WORK_ORDER,
    Attachment.Kind.SERVICE_BOOK: MAX_SERVICE_BOOK,
}

_PHOTO_KINDS = {
    Attachment.Kind.DASHBOARD,
    Attachment.Kind.VEHICLE,
    Attachment.Kind.DEFECT,
}


class AttachmentError(ValueError):
    pass


def _read(uploaded) -> bytes:
    return uploaded.read()


@transaction.atomic
def attach(report, kind: str, view: str, uploaded) -> Attachment:
    """
    Сохранить загруженный файл как вложение отчёта. Для одиночных слотов —
    заменить прежний файл в этом слоте. Бросает AttachmentError при нарушении
    формата, размера или лимита.
    """
    data = _read(uploaded)
    if not data:
        raise AttachmentError("Пустой файл.")

    is_doc_kind = kind in (Attachment.Kind.WORK_ORDER, Attachment.Kind.SERVICE_BOOK)

    if kind in _PHOTO_KINDS:
        main_bytes, thumb_bytes = images.process_photo(data)
        ext, content_type = "jpg", "image/jpeg"
    elif is_doc_kind:
        sniffed = images.validate_document(data)
        if sniffed.is_photo:
            main_bytes, thumb_bytes = images.process_photo(data)
            ext, content_type = "jpg", "image/jpeg"
        else:
            main_bytes, thumb_bytes = data, None
            ext, content_type = "pdf", "application/pdf"
    else:
        raise AttachmentError("Неизвестный тип вложения.")

    if kind in _SINGLE_SLOT_KINDS:
        _replace_single(report, kind, view)
    elif kind in _MULTI_LIMITS:
        count = report.attachments.filter(kind=kind).count()
        if count >= _MULTI_LIMITS[kind]:
            raise AttachmentError(
                f"Достигнут предел вложений этого типа: {_MULTI_LIMITS[kind]}."
            )

    rel = storage.save_bytes(report, kind, ext, main_bytes)
    if thumb_bytes is not None:
        storage.save_thumb(rel, thumb_bytes)

    return Attachment.objects.create(
        report=report,
        kind=kind,
        view=view or Attachment.View.NONE,
        stored_path=rel,
        content_type=content_type,
        size_bytes=len(main_bytes),
    )


def _replace_single(report, kind: str, view: str) -> None:
    existing = report.attachments.filter(kind=kind)
    if kind == Attachment.Kind.VEHICLE:
        existing = existing.filter(view=view)
    for att in existing:
        storage.delete_rel(att.stored_path)
        att.delete()


def remove(report, attachment_id: int) -> bool:
    """Удалить вложение отчёта (до отправки). True, если удалено."""
    att = report.attachments.filter(pk=attachment_id).first()
    if att is None:
        return False
    storage.delete_rel(att.stored_path)
    att.delete()
    return True
