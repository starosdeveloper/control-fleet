"""
Автоматические проверки Шага 1 (ТЗ §10, §11): изоляция доступа, вход по
активной паре, расчёт ТО (§6) и валидаторы идентификаторов (§4).

Значения ТО сверяются с числами из спецификации §6, а не с собственной
арифметикой теста.
"""
from __future__ import annotations

import io
import shutil
import tempfile
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

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


# --- Шаг 2: отчёт водителя ---------------------------------------------------

def make_jpeg(size=(1200, 900), color=(120, 130, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class ReadingClassifyTests(TestCase):
    """ТЗ §6, критерий «Спорный пробег» — чистая логика classify_reading."""

    def c(self, value, when, prior, same_date=None, jump=3000, conflict=1000):
        from .readings import classify_reading
        return classify_reading(value, when, prior, same_date or [], jump, conflict)

    def test_accept_plain_growth(self):
        v = self.c(160_000, date(2026, 9, 30), [(150_000, date(2026, 8, 30))])
        self.assertTrue(v.accepted)

    def test_reject_backwards(self):
        # Позднее меньшее значение → на проверку.
        v = self.c(140_000, date(2026, 9, 30), [(150_000, date(2026, 8, 30))])
        self.assertFalse(v.accepted)

    def test_reject_daily_jump(self):
        # Скачок > 3000 км/сут: +10 000 за 1 день.
        v = self.c(160_000, date(2026, 9, 1), [(150_000, date(2026, 8, 31))])
        self.assertFalse(v.accepted)

    def test_accept_jump_over_many_days(self):
        # +10 000 за 10 дней = 1000 км/сут — в пределах нормы.
        v = self.c(160_000, date(2026, 9, 10), [(150_000, date(2026, 8, 31))])
        self.assertTrue(v.accepted)

    def test_reject_source_conflict_same_date(self):
        v = self.c(160_000, date(2026, 9, 30), [], same_date=[162_000])
        self.assertFalse(v.accepted)

    def test_first_reading_accepted(self):
        v = self.c(100_000, date(2026, 9, 30), [])
        self.assertTrue(v.accepted)


class CompletenessTests(TestCase):
    """ТЗ §5 экран 3: краткая проверка полноты; §6: ТО без документа не блокирует."""

    def setUp(self):
        from .models import Report
        self.tenant = Tenant.objects.create(inn="7700000001")
        self.vehicle = Vehicle.objects.create(vin="TRUCKFLEET0000001")
        self.assignment = Assignment.objects.create(
            vehicle=self.vehicle, tenant=self.tenant, started_at=timezone.now()
        )
        self.draft = Report.objects.create(
            vehicle=self.vehicle, assignment=self.assignment,
            period_month=date.today().replace(day=1), state=Report.State.DRAFT,
            idempotency_key="k1",
        )

    def _add_all_photos(self):
        from .models import Attachment
        Attachment.objects.create(report=self.draft, kind=Attachment.Kind.DASHBOARD,
                                  stored_path="x", content_type="image/jpeg", size_bytes=1)
        for v in (Attachment.View.FRONT, Attachment.View.BACK, Attachment.View.LEFT, Attachment.View.RIGHT):
            Attachment.objects.create(report=self.draft, kind=Attachment.Kind.VEHICLE,
                                      view=v, stored_path="x", content_type="image/jpeg", size_bytes=1)

    def test_empty_draft_many_errors(self):
        from .submit import completeness_errors
        self.assertTrue(completeness_errors(self.draft))

    def test_complete_no_defects(self):
        from .models import Report
        from .submit import completeness_errors
        self.draft.odometer_value_km = 166_000
        self.draft.maint_choice = Report.MaintChoice.UNKNOWN
        self.draft.defects = Report.Defects.NONE
        self.draft.save()
        self._add_all_photos()
        self.assertEqual(completeness_errors(self.draft), [])

    def test_defects_require_comment(self):
        from .models import Report
        from .submit import completeness_errors
        self.draft.odometer_value_km = 166_000
        self.draft.maint_choice = Report.MaintChoice.UNKNOWN
        self.draft.defects = Report.Defects.HAS
        self.draft.comment = ""
        self.draft.save()
        self._add_all_photos()
        self.assertIn("комментарий обязателен", " ".join(completeness_errors(self.draft)))

    def test_maint_mileage_cannot_exceed_odometer(self):
        from .models import Report
        from .submit import completeness_errors
        self.draft.odometer_value_km = 100_000
        self.draft.maint_choice = Report.MaintChoice.DONE
        self.draft.maint_date = date.today()
        self.draft.maint_mileage_km = 120_000
        self.draft.save()
        self._add_all_photos()
        self.assertIn("не может превышать", " ".join(completeness_errors(self.draft)))

    def test_overdue_does_not_block(self):
        # Просрочка ТО не должна попадать в ошибки полноты.
        from .models import Maintenance, OdometerReading, Report
        Maintenance.objects.create(vehicle=self.vehicle, performed_on=date(2026, 1, 1),
                                   mileage_km=120_000, status=Maintenance.Status.CONFIRMED,
                                   basis=Maintenance.Basis.INITIAL)
        self.draft.odometer_value_km = 200_000  # далеко за порогом
        self.draft.maint_choice = Report.MaintChoice.NOT_DONE
        self.draft.save()
        self._add_all_photos()
        from .submit import completeness_errors
        self.assertEqual(completeness_errors(self.draft), [])


class SubmitFlowTests(TestCase):
    """ТЗ §5 экран 4, §6: отправка создаёт показание и ТО «на проверке», идемпотентна."""

    def setUp(self):
        from .models import Report
        self.tenant = Tenant.objects.create(inn="7700000001")
        self.vehicle = Vehicle.objects.create(vin="TRUCKFLEET0000001")
        self.assignment = Assignment.objects.create(
            vehicle=self.vehicle, tenant=self.tenant, started_at=timezone.now()
        )
        self.draft = Report.objects.create(
            vehicle=self.vehicle, assignment=self.assignment,
            period_month=date.today().replace(day=1), state=Report.State.DRAFT,
            idempotency_key="k1", odometer_value_km=166_000,
            maint_choice=Report.MaintChoice.DONE, maint_date=date(2026, 6, 1),
            maint_mileage_km=160_000, maint_no_document=True, maint_reason="нет документа",
        )

    def test_submit_creates_reading_and_pending_maintenance(self):
        from .models import Maintenance, OdometerReading, Report
        from .submit import submit_report
        report = submit_report(self.draft)
        self.assertEqual(report.state, Report.State.SUBMITTED)
        self.assertIsNotNone(report.submitted_at)
        reading = OdometerReading.objects.get(report=report)
        self.assertEqual(reading.value_km, 166_000)
        self.assertEqual(reading.source, OdometerReading.Source.DRIVER)
        m = Maintenance.objects.get(report=report)
        self.assertEqual(m.status, Maintenance.Status.REPORTED)  # не подтверждено автоматически
        self.assertTrue(m.no_document)

    def test_submit_idempotent_no_duplicate(self):
        from .models import OdometerReading, Report
        from .submit import submit_report
        r1 = submit_report(self.draft)
        r2 = submit_report(r1)  # повторная отправка
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(OdometerReading.objects.filter(report=r1).count(), 1)
        self.assertEqual(Report.objects.filter(assignment=self.assignment).count(), 1)


class ImageProcessTests(TestCase):
    def test_process_photo_returns_jpeg_and_thumb(self):
        from .images import process_photo, sniff
        main, thumb = process_photo(make_jpeg())
        self.assertEqual(sniff(main).kind, "jpeg")
        self.assertEqual(sniff(thumb).kind, "jpeg")
        self.assertLessEqual(len(main), 2 * 1024 * 1024)

    def test_sniff_rejects_garbage(self):
        from .images import ImageError, sniff
        with self.assertRaises(ImageError):
            sniff(b"this is not an image at all")

    def test_document_pdf_ok(self):
        from .images import validate_document
        s = validate_document(b"%PDF-1.4\n...")
        self.assertEqual(s.kind, "pdf")
        self.assertFalse(s.is_photo)


_TMP_MEDIA = tempfile.mkdtemp(prefix="cf_test_media_")


@override_settings(PRIVATE_MEDIA_ROOT=_TMP_MEDIA)
class DriverReportE2ETests(TestCase):
    """ТЗ §5: сквозной путь по экранам через HTTP-клиент, с загрузкой фото."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(inn="7700000001")
        self.vehicle = Vehicle.objects.create(vin="TRUCKFLEET0000001")
        Assignment.objects.create(
            vehicle=self.vehicle, tenant=self.tenant, started_at=timezone.now()
        )
        self.client.post(reverse("driver_login"),
                         {"inn": "7700000001", "vin": "TRUCKFLEET0000001"})

    def _img(self, name):
        return SimpleUploadedFile(name, make_jpeg(), content_type="image/jpeg")

    def test_full_flow_to_confirmation(self):
        from .models import Attachment, OdometerReading, Report
        # Экран 1: показание + фото одометра + ТО неизвестно.
        self.client.post(reverse("driver_report_step1"), {
            "odometer_value_km": "166000", "maint_choice": "unknown",
            "photo_dashboard": self._img("dash.jpg"),
        })
        draft = Report.objects.get(assignment__vehicle=self.vehicle, state=Report.State.DRAFT)
        self.assertEqual(draft.odometer_value_km, 166000)
        self.assertTrue(draft.attachments.filter(kind=Attachment.Kind.DASHBOARD).exists())

        # Экран 2: четыре ракурса.
        for v in ("front", "back", "left", "right"):
            self.client.post(reverse("driver_report_step2"), {f"photo_{v}": self._img(f"{v}.jpg")})
        self.assertEqual(draft.attachments.filter(kind=Attachment.Kind.VEHICLE).count(), 4)

        # Экран 3: без замечаний, отправка.
        resp = self.client.post(reverse("driver_report_step3"),
                                {"defects": "none", "action": "submit"})
        self.assertRedirects(resp, reverse("driver_report_done"))
        report = Report.objects.get(state=Report.State.SUBMITTED)
        self.assertEqual(OdometerReading.objects.filter(report=report).count(), 1)

        # Экран 4: подтверждение показывает номер.
        done = self.client.get(reverse("driver_report_done"))
        self.assertContains(done, report.number)

    def test_cannot_submit_without_required_photos(self):
        from .models import Report
        self.client.post(reverse("driver_report_step1"),
                         {"odometer_value_km": "166000", "maint_choice": "unknown"})
        resp = self.client.post(reverse("driver_report_step3"),
                                {"defects": "none", "action": "submit"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "фото")  # перечислены недостающие пункты
        self.assertFalse(Report.objects.filter(state=Report.State.SUBMITTED).exists())

    def test_foreign_attachment_not_served(self):
        # Вложение чужого ТС недоступно по прямой ссылке (ТЗ §9).
        from .models import Attachment, Report
        other_v = Vehicle.objects.create(vin="TRUCKFLEET0000002")
        other_t = Tenant.objects.create(inn="500100732259")
        other_a = Assignment.objects.create(vehicle=other_v, tenant=other_t, started_at=timezone.now())
        other_r = Report.objects.create(vehicle=other_v, assignment=other_a,
                                        period_month=date.today().replace(day=1),
                                        state=Report.State.DRAFT, idempotency_key="k2")
        att = Attachment.objects.create(report=other_r, kind=Attachment.Kind.DASHBOARD,
                                        stored_path="x", content_type="image/jpeg", size_bytes=1)
        # Текущая сессия вошла на VIN ...0001, пытается тянуть файл ...0002.
        resp = self.client.get(reverse("driver_file", args=[att.id]))
        self.assertEqual(resp.status_code, 404)
