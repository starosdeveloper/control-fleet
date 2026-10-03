"""
Сверить восстановленную копию с манифестом, снятым при резервном копировании
(ТЗ §10). Критерий должен уметь упасть — ненулевой код выхода при расхождении,
а не строка в логе, которой позже поверят на слово.

Источник истины — не сам дамп (он бы согласился с любой своей ошибкой: пустая
таблица и правильная одинаково «есть», пока не считать), а независимый манифест.
Открывает восстановленную базу напрямую через `sqlite3`, в обход Django ORM и
текущего `DATABASES['default']` — иначе легко свериться с рабочей базой процесса
вместо реально восстановленного файла, и проверка замолчит именно тогда, когда
нужна.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

# Таблица в восстановленной sqlite для каждого ключа манифеста
# (app_label_modelname в нижнем регистре — соглашение Django по умолчанию).
_TABLE_BY_KEY = {
    "vehicle": "fleet_vehicle",
    "tenant": "fleet_tenant",
    "assignment": "fleet_assignment",
    "manager_assignment": "fleet_managerassignment",
    "report": "fleet_report",
    "odometer_reading": "fleet_odometerreading",
    "maintenance": "fleet_maintenance",
    "attachment": "fleet_attachment",
    "import_batch": "fleet_importbatch",
}


class Command(BaseCommand):
    help = "Сверить восстановленную sqlite-копию и файлы вложений с манифестом (ТЗ §10)."

    def add_arguments(self, parser):
        parser.add_argument("--db", required=True, help="Путь к восстановленному db.sqlite3")
        parser.add_argument("--media", required=True, help="Корень восстановленного private_media")
        parser.add_argument("--manifest", required=True, help="manifest.json, снятый при бэкапе")

    def handle(self, *args, **options):
        db_path = Path(options["db"])
        media_root = Path(options["media"])
        manifest_path = Path(options["manifest"])

        if not db_path.exists():
            raise CommandError(f"Восстановленная база не найдена: {db_path}")
        if not manifest_path.exists():
            raise CommandError(f"Манифест не найден: {manifest_path}")
        if not media_root.exists():
            raise CommandError(f"Каталог вложений не найден: {media_root}")

        expected = json.loads(manifest_path.read_text(encoding="utf-8"))["counts"]

        conn = sqlite3.connect(str(db_path))
        try:
            mismatches = []
            for key, table in _TABLE_BY_KEY.items():
                actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                want = expected.get(key)
                ok = actual == want
                self.stdout.write(
                    f"{'OK' if ok else 'MISMATCH'} {key}: восстановлено {actual}, манифест {want}"
                )
                if not ok:
                    mismatches.append(key)

            # Каждому Attachment.stored_path обязан соответствовать файл на диске —
            # строка в БД без файла это не найденное вложение, а потерянное.
            paths = [row[0] for row in conn.execute("SELECT stored_path FROM fleet_attachment")]
        finally:
            conn.close()

        missing = [p for p in paths if not (media_root / p).exists()]
        files_ok = not missing
        self.stdout.write(
            f"{'OK' if files_ok else 'MISMATCH'} файлы вложений: "
            f"{len(paths) - len(missing)}/{len(paths)} найдено на диске"
        )
        if missing:
            for p in missing[:10]:
                self.stdout.write(f"  отсутствует: {p}")
            if len(missing) > 10:
                self.stdout.write(f"  ... и ещё {len(missing) - 10}")

        if mismatches or missing:
            raise CommandError(
                f"Восстановление НЕ подтверждено: расхождение таблиц {mismatches}, "
                f"пропавших файлов {len(missing)} из {len(paths)}."
            )

        self.stdout.write(
            self.style.SUCCESS("Восстановление подтверждено: все счётчики и файлы совпали.")
        )
