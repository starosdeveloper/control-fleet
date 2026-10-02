"""
Тестовые данные Шага 1 (ТЗ §10): 10 VIN, два ИНН, два менеджера, администратор,
назначения и закрепления, а также несколько записей ТО/пробега для демонстрации
всех состояний расчёта (§6). Команда идемпотентна — повторный запуск не плодит дубли.
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from fleet.access import ADMIN_GROUP, MANAGER_GROUP
from fleet.models import (
    Assignment,
    Maintenance,
    ManagerAssignment,
    OdometerReading,
    Tenant,
    Vehicle,
)

DEMO_PASSWORD = "Fleet!demo2026"
TENANT_A_INN = "7700000001"      # 10 цифр
TENANT_B_INN = "500100732259"    # 12 цифр


class Command(BaseCommand):
    help = "Наполнить базу тестовыми данными Шага 1 (идемпотентно)."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()

        admin_group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
        manager_group, _ = Group.objects.get_or_create(name=MANAGER_GROUP)

        # Администратор.
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True, "email": ""},
        )
        if created:
            admin.set_password(DEMO_PASSWORD)
            admin.save()
        admin.groups.add(admin_group)

        # Два менеджера (сотрудники со штатным паролем).
        managers = []
        for name in ("manager1", "manager2"):
            m, created = User.objects.get_or_create(
                username=name, defaults={"is_staff": True, "is_superuser": False}
            )
            if created:
                m.set_password(DEMO_PASSWORD)
                m.save()
            m.groups.add(manager_group)
            managers.append(m)

        # Два арендатора.
        tenant_a, _ = Tenant.objects.get_or_create(inn=TENANT_A_INN)
        tenant_b, _ = Tenant.objects.get_or_create(inn=TENANT_B_INN)

        # Десять ТС с валидными VIN (без I, O, Q).
        vehicles = []
        for i in range(1, 11):
            vin = f"TRUCKFLEET{i:07d}"
            v, _ = Vehicle.objects.get_or_create(
                vin=vin,
                defaults={
                    "plate": f"А{i:03d}АА750",
                    "model": "КАМАЗ-5490" if i % 2 else "Volvo FH",
                    "vehicle_type": "седельный тягач",
                },
            )
            vehicles.append(v)

        now = timezone.now()

        # Назначения: ТС 1–6 арендатору A, 7–10 арендатору B.
        for idx, v in enumerate(vehicles, start=1):
            tenant = tenant_a if idx <= 6 else tenant_b
            assignment, _ = Assignment.objects.get_or_create(
                vehicle=v, ended_at__isnull=True,
                defaults={"tenant": tenant, "started_at": now},
            )
            # Закрепление менеджера: 1–5 → manager1, 6–10 → manager2.
            manager = managers[0] if idx <= 5 else managers[1]
            ManagerAssignment.objects.get_or_create(
                assignment=assignment, ended_at__isnull=True,
                defaults={"manager": manager, "started_at": now},
            )

        self._seed_maintenance_demo(vehicles)

        self.stdout.write(self.style.SUCCESS("Тестовые данные готовы."))
        self.stdout.write(f"  ТС: {Vehicle.objects.count()}")
        self.stdout.write(f"  Арендаторы: ИНН {TENANT_A_INN} (ТС 1–6), ИНН {TENANT_B_INN} (ТС 7–10)")
        self.stdout.write("  Сотрудники: admin (администратор), manager1 (ТС 1–5), manager2 (ТС 6–10)")
        self.stdout.write(f"  Пароль сотрудников: {DEMO_PASSWORD}")
        self.stdout.write("  Пример входа водителя: ИНН 7700000001 + VIN TRUCKFLEET0000001")

    def _seed_maintenance_demo(self, vehicles: list[Vehicle]) -> None:
        """Записи, покрывающие все состояния §6: зелёный/жёлтый/красный/серый."""
        base = 120_000
        interval = 50_000  # следующее ТО → 170 000
        cases = {
            0: 150_000,  # остаток 20 000 → зелёный
            1: 166_000,  # остаток 4 000  → жёлтый
            2: 170_000,  # остаток 0      → ТО требуется
            3: 172_500,  # остаток −2 500 → просрочено
            # vehicles[4] — без подтверждённого ТО → серый «нет данных»
        }
        for idx, current in cases.items():
            v = vehicles[idx]
            Maintenance.objects.get_or_create(
                vehicle=v, mileage_km=base, status=Maintenance.Status.CONFIRMED,
                defaults={
                    "performed_on": date(2026, 1, 15),
                    "basis": Maintenance.Basis.INITIAL,
                    "reason": "Начальная база (демо)",
                    "confirmed_at": timezone.now(),
                },
            )
            OdometerReading.objects.get_or_create(
                vehicle=v, source=OdometerReading.Source.GPS, value_km=current,
                measured_at=date(2026, 9, 30),
                defaults={"verification": OdometerReading.Verification.ACCEPTED},
            )
