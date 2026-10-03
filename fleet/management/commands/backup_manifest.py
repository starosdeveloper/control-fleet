"""
Снять счётчики ключевых таблиц в JSON перед резервным копированием (ТЗ §10).

Манифест — независимая от самого дампа точка сверки: после восстановления
`verify_restore` сравнивает восстановленную копию не с собой, а с этим файлом,
снятым ДО копирования запросом к живой базе. Если манифест брать из самого
дампа, проверка согласится с любой его ошибкой — ровно та ловушка, которой
посвящено правило «тест на раскладку сверяет с константой, а не с собственной
арифметикой» (docs/ROADMAP.md, «Дисциплина кода»), только здесь это не
раскладка, а состав базы.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from fleet.models import (
    Assignment,
    Attachment,
    ImportBatch,
    Maintenance,
    ManagerAssignment,
    OdometerReading,
    Report,
    Tenant,
    Vehicle,
)

# Порядок и набор — тот же, что читает `verify_restore` (общий источник истины
# для имён ключей; таблицы называет сама команда проверки).
_COUNTERS = {
    "vehicle": Vehicle,
    "tenant": Tenant,
    "assignment": Assignment,
    "manager_assignment": ManagerAssignment,
    "report": Report,
    "odometer_reading": OdometerReading,
    "maintenance": Maintenance,
    "attachment": Attachment,
    "import_batch": ImportBatch,
}


class Command(BaseCommand):
    help = "Записать манифест счётчиков таблиц перед резервной копией (ТЗ §10)."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Путь к manifest.json")

    def handle(self, *args, **options):
        counts = {key: model.objects.count() for key, model in _COUNTERS.items()}
        manifest = {"created_at": timezone.now().isoformat(), "counts": counts}

        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"manifest: {out}"))
        for key, value in counts.items():
            self.stdout.write(f"  {key}: {value}")
