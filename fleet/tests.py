"""
Автоматические проверки Шага 1 (ТЗ §10, §11): изоляция доступа, вход по
активной паре, расчёт ТО (§6) и валидаторы идентификаторов (§4).

Значения ТО сверяются с числами из спецификации §6, а не с собственной
арифметикой теста.
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .access import (
    ADMIN_GROUP,
    MANAGER_GROUP,
    can_staff_access_vehicle,
    is_admin,
    is_manager,
    resolve_active_assignment,
    vehicles_for_staff,
)
from .maintenance import Color, MaintStatus, maintenance_state
from .models import Assignment, ManagerAssignment, Tenant, Vehicle
from .validators import (
    is_valid_inn,
    is_valid_vin,
    normalize_vin,
    validate_inn,
    validate_vin,
)


class MaintenanceCalcTests(TestCase):
    """Расчёт §6: границы и пример со значениями из спецификации."""

    INTERVAL = 50_000
    WARNING = 5_000

    def state(self, base, current):
        return maintenance_state(base, current, self.INTERVAL, self.WARNING)

    def test_no_base(self):
        s = self.state(None, 100_000)
        self.assertEqual(s.status, MaintStatus.NO_DATA)
        self.assertEqual(s.color, Color.GRAY)

    def test_green_example_remainder_above_warning(self):
        # База 120 000 → следующее 170 000; текущий 150 000 → остаток 20 000.
        s = self.state(120_000, 150_000)
        self.assertEqual(s.next_due_km, 170_000)
        self.assertEqual(s.remainder_km, 20_000)
        self.assertEqual(s.color, Color.GREEN)

    def test_yellow_example_166000(self):
        # §6 пример: при 166 000 остаток 4 000.
        s = self.state(120_000, 166_000)
        self.assertEqual(s.remainder_km, 4_000)
        self.assertEqual(s.status, MaintStatus.SOON)
        self.assertEqual(s.color, Color.YELLOW)

    def test_boundary_warning_exactly_5000_is_yellow(self):
        # Остаток ровно 5 000 — это «от 1 до 5 000», жёлтый, не зелёный.
        s = self.state(120_000, 165_000)
        self.assertEqual(s.remainder_km, 5_000)
        self.assertEqual(s.color, Color.YELLOW)

    def test_boundary_warning_plus_one_is_green(self):
        s = self.state(120_000, 164_999)
        self.assertEqual(s.remainder_km, 5_001)
        self.assertEqual(s.color, Color.GREEN)

    def test_due_example_170000(self):
        # §6 пример: при 170 000 ТО требуется (остаток 0).
        s = self.state(120_000, 170_000)
        self.assertEqual(s.remainder_km, 0)
        self.assertEqual(s.status, MaintStatus.DUE)
        self.assertEqual(s.color, Color.RED)

    def test_overdue_example_172500(self):
        # §6 пример: при 172 500 просрочка 2 500.
        s = self.state(120_000, 172_500)
        self.assertEqual(s.remainder_km, -2_500)
        self.assertEqual(s.status, MaintStatus.OVERDUE)
        self.assertEqual(s.text, "ТО просрочено на 2500 км")

    def test_confirmed_newer_moves_threshold(self):
        # §6 пример: подтверждённое ТО на 172 000 → следующий порог 222 000.
        s = self.state(172_000, 180_000)
        self.assertEqual(s.next_due_km, 222_000)


class ValidatorTests(TestCase):
    def test_vin_ok(self):
        self.assertTrue(is_valid_vin("TRUCKFLEET0000001"))
        validate_vin("WAUZZZ8V9KA123456"[:17])

    def test_vin_rejects_ioq(self):
        for bad in ("TRUCKFLEETI000001", "TRUCKFLEETO000001", "TRUCKFLEETQ000001"):
            self.assertFalse(is_valid_vin(bad))
            with self.assertRaises(ValidationError):
                validate_vin(bad)

    def test_vin_length(self):
        self.assertFalse(is_valid_vin("TRUCK0001"))

    def test_vin_normalize(self):
        self.assertEqual(normalize_vin("  truckfleet0000001 "), "TRUCKFLEET0000001")

    def test_inn_10_and_12(self):
        self.assertTrue(is_valid_inn("7700000001"))
        self.assertTrue(is_valid_inn("500100732259"))

    def test_inn_bad(self):
        for bad in ("123", "77000000011", "abcdefghij"):
            self.assertFalse(is_valid_inn(bad))
            with self.assertRaises(ValidationError):
                validate_inn(bad)


class AccessIsolationTests(TestCase):
    """ТЗ §4, §7, §11 «Доступ»: изоляция проверяется до остального."""

    def setUp(self):
        User = get_user_model()
        self.admin_group = Group.objects.create(name=ADMIN_GROUP)
        self.manager_group = Group.objects.create(name=MANAGER_GROUP)

        self.admin = User.objects.create_user("admin", is_staff=True, is_superuser=True)
        self.m1 = User.objects.create_user("m1", is_staff=True)
        self.m2 = User.objects.create_user("m2", is_staff=True)
        self.stranger = User.objects.create_user("x", is_staff=True)
        self.m1.groups.add(self.manager_group)
        self.m2.groups.add(self.manager_group)

        self.tenant = Tenant.objects.create(inn="7700000001")
        now = timezone.now()
        self.vehicles = []
        for i in range(1, 11):
            v = Vehicle.objects.create(vin=f"TRUCKFLEET{i:07d}")
            a = Assignment.objects.create(vehicle=v, tenant=self.tenant, started_at=now)
            manager = self.m1 if i <= 5 else self.m2
            ManagerAssignment.objects.create(assignment=a, manager=manager, started_at=now)
            self.vehicles.append(v)

    def test_roles(self):
        self.assertTrue(is_admin(self.admin))
        self.assertTrue(is_manager(self.m1))
        self.assertFalse(is_manager(self.stranger))
        self.assertFalse(is_admin(self.m1))

    def test_admin_sees_all(self):
        self.assertEqual(vehicles_for_staff(self.admin).count(), 10)

    def test_manager_sees_only_own(self):
        v1 = set(vehicles_for_staff(self.m1).values_list("vin", flat=True))
        v2 = set(vehicles_for_staff(self.m2).values_list("vin", flat=True))
        self.assertEqual(len(v1), 5)
        self.assertEqual(len(v2), 5)
        self.assertEqual(v1 & v2, set())  # пересечения нет

    def test_stranger_sees_nothing(self):
        self.assertEqual(vehicles_for_staff(self.stranger).count(), 0)

    def test_manager_cannot_access_foreign_vehicle(self):
        own = self.vehicles[0]          # ТС №1 — у m1
        foreign = self.vehicles[9]      # ТС №10 — у m2
        self.assertTrue(can_staff_access_vehicle(self.m1, own))
        self.assertFalse(can_staff_access_vehicle(self.m1, foreign))

    def test_closing_assignment_drops_manager_visibility(self):
        a = self.vehicles[0].active_assignment()
        a.ended_at = timezone.now()
        a.save()
        self.assertFalse(can_staff_access_vehicle(self.m1, self.vehicles[0]))


class DriverLoginTests(TestCase):
    """ТЗ §4: вход по активной паре, единое сообщение об отказе, изоляция одного VIN."""

    def setUp(self):
        self.client = Client()
        self.tenant_a = Tenant.objects.create(inn="7700000001")
        self.tenant_b = Tenant.objects.create(inn="500100732259")
        self.v1 = Vehicle.objects.create(vin="TRUCKFLEET0000001")
        self.v2 = Vehicle.objects.create(vin="TRUCKFLEET0000002")
        now = timezone.now()
        self.a1 = Assignment.objects.create(vehicle=self.v1, tenant=self.tenant_a, started_at=now)
        self.a2 = Assignment.objects.create(vehicle=self.v2, tenant=self.tenant_b, started_at=now)

    def test_resolve_correct_pair(self):
        a = resolve_active_assignment("7700000001", "TRUCKFLEET0000001")
        self.assertIsNotNone(a)
        self.assertEqual(a.vehicle, self.v1)

    def test_resolve_wrong_crossed_pair(self):
        # Существуют оба значения, но не как пара → отказ.
        self.assertIsNone(resolve_active_assignment("7700000001", "TRUCKFLEET0000002"))

    def test_resolve_closed_assignment(self):
        self.a1.ended_at = timezone.now()
        self.a1.save()
        self.assertIsNone(resolve_active_assignment("7700000001", "TRUCKFLEET0000001"))

    def test_resolve_archived_vehicle(self):
        self.v1.archived_at = timezone.now()
        self.v1.save()
        self.assertIsNone(resolve_active_assignment("7700000001", "TRUCKFLEET0000001"))

    def test_login_view_success_redirects_to_cabinet(self):
        resp = self.client.post(
            reverse("driver_login"),
            {"inn": "7700000001", "vin": "truckfleet0000001"},  # нижний регистр нормализуется
        )
        self.assertRedirects(resp, reverse("driver_home"))

    def test_login_view_failure_shows_single_message(self):
        resp = self.client.post(
            reverse("driver_login"),
            {"inn": "7700000001", "vin": "TRUCKFLEET0000002"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Не удалось войти")

    def test_cabinet_requires_login(self):
        resp = self.client.get(reverse("driver_home"))
        self.assertRedirects(resp, reverse("driver_login"))

    def test_closing_assignment_drops_driver_session(self):
        self.client.post(
            reverse("driver_login"),
            {"inn": "7700000001", "vin": "TRUCKFLEET0000001"},
        )
        self.assertEqual(self.client.get(reverse("driver_home")).status_code, 200)
        # Смена арендатора: старое назначение закрыто → сессия теряет доступ.
        self.a1.ended_at = timezone.now()
        self.a1.save()
        self.assertRedirects(self.client.get(reverse("driver_home")), reverse("driver_login"))

    def test_bad_format_shows_field_error_not_generic(self):
        resp = self.client.post(
            reverse("driver_login"), {"inn": "123", "vin": "short"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ИНН — строка из 10 или 12 цифр")
        self.assertNotContains(resp, "Не удалось войти")
