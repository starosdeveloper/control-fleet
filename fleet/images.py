"""
Проверка и обработка вложений (ТЗ §9).

Формат определяется по реальному содержимому (магические байты), а не по
расширению. Фото (JPEG, PNG, HEIC) приводятся к JPEG: ориентация по EXIF
применяется и затем метаданные (включая геолокацию) отбрасываются, создаётся
миниатюра, целевой размер — до 2 МБ при сохранении читаемости. Документы ТО —
PDF и те же форматы фото, до 15 МБ.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

# Регистрируем декодер HEIC для Pillow.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIC_OK = True
except Exception:  # pragma: no cover - отсутствие плагина не должно ронять импорт
    _HEIC_OK = False

MAX_SOURCE_BYTES = 15 * 1024 * 1024       # до 15 МБ на исходный файл
PHOTO_TARGET_BYTES = 2 * 1024 * 1024      # цель после обработки
PHOTO_MAX_DIM = 2200                       # максимальная сторона, px
THUMB_DIM = 320


class ImageError(ValueError):
    """Вложение не прошло проверку формата/размера."""


@dataclass(frozen=True)
class Sniffed:
    kind: str   # "jpeg" | "png" | "heic" | "pdf"
    is_photo: bool


def sniff(data: bytes) -> Sniffed:
    """Определить формат по сигнатуре. Бросить ImageError на неизвестном."""
    if data[:3] == b"\xff\xd8\xff":
        return Sniffed("jpeg", True)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return Sniffed("png", True)
    if data[4:8] == b"ftyp" and data[8:12] in (
        b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1",
    ):
        return Sniffed("heic", True)
    if data[:5] == b"%PDF-":
        return Sniffed("pdf", False)
    raise ImageError("Неподдерживаемый формат файла.")


def _strip_and_orient(img: Image.Image) -> Image.Image:
    """Применить поворот по EXIF и вернуть копию без метаданных."""
    img = ImageOps.exif_transpose(img)  # учесть ориентацию камеры
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Пересоздание из пикселей отбрасывает EXIF/GPS целиком.
    clean = Image.new(img.mode, img.size)
    clean.putdata(list(img.getdata()))
    return clean


def process_photo(data: bytes) -> tuple[bytes, bytes]:
    """
    Проверить и обработать фото. Вернуть (jpeg_основное, jpeg_миниатюра).
    Бросить ImageError при неверном формате или нечитаемом изображении.
    """
    if len(data) > MAX_SOURCE_BYTES:
        raise ImageError("Файл больше 15 МБ.")
    sniffed = sniff(data)
    if not sniffed.is_photo:
        raise ImageError("Ожидалось изображение (JPEG, PNG или HEIC).")
    if sniffed.kind == "heic" and not _HEIC_OK:
        raise ImageError("Формат HEIC сейчас не поддерживается сервером.")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001 - любое непрочтение → отказ
        raise ImageError("Не удалось прочитать изображение.") from exc

    img = _strip_and_orient(img)

    # Ограничить максимальную сторону, сохраняя читаемость одометра.
    full = img.copy()
    full.thumbnail((PHOTO_MAX_DIM, PHOTO_MAX_DIM), Image.LANCZOS)
    main_bytes = _encode_under_limit(full, PHOTO_TARGET_BYTES)

    thumb = img.copy()
    thumb.thumbnail((THUMB_DIM, THUMB_DIM), Image.LANCZOS)
    thumb_buf = io.BytesIO()
    thumb.convert("RGB").save(thumb_buf, format="JPEG", quality=80, optimize=True)

    return main_bytes, thumb_buf.getvalue()


def _encode_under_limit(img: Image.Image, limit: int) -> bytes:
    """
    Кодировать JPEG, снижая качество до попадания в лимит, но не ниже порога,
    за которым теряется текст одометра — тогда вернуть лучший доступный результат.
    """
    rgb = img.convert("RGB")
    for quality in (88, 82, 75, 68, 60):
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) <= limit:
            return out
    # Не влезли даже на q=60: сохранить читаемость, отдать q=60.
    return out


def validate_document(data: bytes) -> Sniffed:
    """Проверить документ ТО (PDF или фото), до 15 МБ. Вернуть формат."""
    if len(data) > MAX_SOURCE_BYTES:
        raise ImageError("Файл больше 15 МБ.")
    return sniff(data)
