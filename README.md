# Control Fleet — fleet control web system

Web system for monthly control of a heavy-truck rental fleet. Drivers submit a
monthly report (odometer photo, mileage, four vehicle angles, maintenance info)
from a phone browser; staff (managers and an administrator) review reports,
photos and maintenance state, import GPS odometer readings from Excel, and track
fleet-wide indicators.

Built on Django + server-rendered HTML templates + a little JavaScript,
PostgreSQL in production (SQLite for local development), closed file storage on
the server. Timezone `Europe/Moscow`. See [`docs/SPEC.md`](docs/SPEC.md) for the
data contracts and [`docs/ROADMAP.md`](docs/ROADMAP.md) (Russian) for stage
criteria and what is still open.

## Current state — Stages 1–3 done

What works and is proven on a live server:

- **Domain models and migrations** for every §9 entity: `Vehicle`, `Tenant`,
  `Assignment`, `ManagerAssignment`, `Report`, `OdometerReading`, `Maintenance`,
  `Attachment`, `ImportBatch`, `AuditLog`.
- **Driver login by the active INN+VIN pair** (not by either identifier alone):
  normalization, format validation shown before submit, single refusal message,
  anti-bruteforce (5 attempts / 15 min per pair and per IP, temporary), 24-hour
  session, logout.
- **Access isolation**, server-side, checked per page/operation: admin sees the
  whole fleet; a manager sees only vehicles currently assigned to them; a driver
  session is locked to the one VIN entered at login, and loses access the moment
  that assignment is closed.
- **Maintenance calculation (§6)** as a pure, host-tested function: next service =
  confirmed-service mileage + interval; remainder drives gray/green/yellow/red.
- **Driver report, four screens (§5)**: dashboard-odometer photo + mileage +
  maintenance block; four mandatory tractor angles + up to five defect photos;
  defects/comment + a completeness check; confirmation with a report number.
  Server-side draft (one per assignment), safe idempotent submission (a double
  submit yields one report), reading-dispute classification (§6), maintenance
  reported by the driver and left "pending" for staff (never auto-confirmed),
  overdue never blocks submission.
- **Closed file storage (§9)**: real-format check by magic bytes, convert to JPEG,
  EXIF orientation applied then metadata (incl. geolocation) stripped, thumbnail,
  target ≤2 MB, ≤15 MB source; server-generated names under
  `vehicles/<ID_VIN>/reports/<YYYY-MM>/report_<id>/…`; served only after a
  per-VIN access check (no public links).
- **Demo data**: 10 vehicles, 2 tenants, 2 managers, 1 admin, assignments and
  maintenance records covering every §6 state.
- **Django admin** as the raw back office, with manager querysets restricted to
  their own vehicles.
- **Staff cabinet (§7)** at `/staff/`: a month dashboard (reporting-fleet size,
  reports/photos submitted with completion share, overdue/due/no-base maintenance,
  vehicles without a current reading), comparison with the previous month (numeric
  diff and share; no percent when the prior base is zero; "month not finished" for
  an incomplete current month); a control table with every §7 column and filters
  (no report, overdue/due/no-base, readings/maintenance pending review, VIN/INN
  search); a VIN card (general data, active assignment, assignment history, reports
  with photos and comments, reading and maintenance history, correction log); staff
  review of disputed readings and maintenance (accept/reject/correct with a
  mandatory reason, the original kept in the audit log; rejecting maintenance never
  changes the prior confirmed base); and export of the filtered table to Excel and
  of a selected report with attachments to a ZIP. The **as-of cut** computes a
  completed month on the last day of that month — history is never recomputed by
  today's mileage. Every page, operation and download is access-checked server-side.

Stage 4 (GPS Excel import, mass fleet onboarding, deployment) is scaffolded in the
models but not yet implemented. See the roadmap.

## Quick start (local development)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # DEBUG=1, DB_ENGINE=sqlite by default

python manage.py migrate
python manage.py seed_demo    # 10 VIN, 2 INN, 2 managers, admin
python manage.py runserver
```

- Driver cabinet: <http://127.0.0.1:8000/> — log in with
  INN `7700000001` + VIN `TRUCKFLEET0000001` (vehicle #1, green state).
- Staff cabinet: <http://127.0.0.1:8000/staff/> — `admin` / `Fleet!demo2026`
  (managers: `manager1`, `manager2`, same password). Raw back office remains at
  <http://127.0.0.1:8000/admin/>.

### Production (PostgreSQL)

Set in `.env`: `DB_ENGINE=postgres`, the `DB_*` credentials, a real
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS=your.domain`,
`DJANGO_SECURE_SSL=1`, and `PRIVATE_MEDIA_ROOT` pointing outside the web root.
The server and backups must be located in Russia (project requirement, §2).

## Tests

```bash
python manage.py test
```

Automatic checks are mandatory for the maintenance calc, access rights and login
(§11); screen scenarios are checked by hand on phones.

Last real run:

```
Found 72 test(s).
System check identified no issues (0 silenced).
Ran 72 tests in 3.739s
OK
```

### Live server check — driver login flow

Raw output from a running `runserver` instance:

```
A) TRUE cross-pair: tenant B INN + tenant A VIN -> must refuse
   Не удалось войти. Проверьте ИНН и VIN или обратитесь к менеджеру.
B) correct pair VIN1 (tenant A) -> redirect, cabinet shows VIN1 GREEN
   login HTTP 302 -> http://127.0.0.1:8099/cabinet/
   class="vin">TRUCKFLEET0000001
   150000 км
   badge green">До ТО осталось 20000 км
C) bad format -> field error, NOT generic refusal
   ИНН — строка из 10 или 12 цифр
   VIN — 17 латинских букв и цифр, без I, O, Q.
D) cabinet without session -> redirect to login
   HTTP 302 -> http://127.0.0.1:8099/
```

### Live server check — full report submission (four screens, with photo upload)

```
=== logged in as VIN1 ===
step1 next HTTP 302 -> http://127.0.0.1:8099/report/step2/
=== 4 angles uploaded ===
step2 next HTTP 302 -> http://127.0.0.1:8099/report/step3/
step3 submit HTTP 302 -> http://127.0.0.1:8099/report/done/
=== screen 4: confirmation ===
Спасибо, данные отправлены
R-000001
02.10.2026

DB after run:
report R-000001 state submitted manager manager1
reading 155000 source driver verification accepted
attachments: 5 {'dashboard': 1, 'vehicle': 4}
stored files under private_media/vehicles/000001_TRUCKFLEET0000001/reports/2026-10/report_1/
```

### Live server check — staff cabinet (§7)

Raw output from a running instance against the demo data (October 2026):

```
=== unauthenticated /staff/ ===
HTTP 302 -> http://127.0.0.1:8111/admin/login/?next=/staff/
=== admin login ===
admin login: 302 -> http://127.0.0.1:8111/staff/

=== admin dashboard, October 2026 — indicator tiles ===
  10  Всего VIN в парке
   1  Передали сведения · 10%
   1  Передали фото ТС
   9  Не передали отчёт
   1  ТО просрочено
   1  ТО требуется
   6  Нет данных о ТО
   6  Без актуального пробега
comparison: 1 против 0 (разница +1, % не рассчитывается); доля 10% против —; Месяц не завершён
maintenance pills in table: «До ТО осталось 15000 км», «Скоро ТО. Осталось 4000 км»,
                            «ТО требуется сейчас», «ТО просрочено на 2500 км», «Нет данных о последнем ТО» ×6

=== export ===
export.xlsx: HTTP 200 type=…spreadsheetml.sheet size=6329  magic=504b (PK, zip)
report/1/zip: HTTP 200 size=2102
  entries: R-000001/{одометр.jpg, ракурс_спереди.jpg, ракурс_сзади.jpg,
           ракурс_слева.jpg, ракурс_справа.jpg, manifest.txt}

=== access isolation (manager1) ===
own  VIN1 card: HTTP 200
frgn VIN6 card: HTTP 404
manager1 dashboard fleet size: 5
report/1/zip as manager2 (not owner): HTTP 404

=== staff correction with mandatory reason (admin) ===
correct POST: HTTP 302 -> http://127.0.0.1:8111/staff/vehicle/1/
audit: reading.correct | 155000 км -> 156000 км | фото читается как 156000 | admin
```

## Project layout

```
controlfleet/        project settings, urls, wsgi/asgi
fleet/               core domain
  models.py          §9 entities
  validators.py      VIN / INN normalization and format (§4)
  maintenance.py     §6 service calculation (pure, host-tested)
  readings.py        §6 odometer-reading dispute classification (pure)
  submit.py          report completeness + atomic idempotent submission (§5, §6)
  attachments.py     attachment intake, slots, limits (§5, §9)
  images.py          format sniff, JPEG convert, EXIF strip, thumbnail (§9)
  storage.py         closed file storage, server-generated paths (§9)
  fileaccess.py      per-VIN protected file serving (§9)
  access.py          role/visibility isolation and active-pair login (§4, §7)
  asof.py            as-of cut selection for a month (pure, host-tested) (§7)
  metrics.py         month-over-month comparison (pure, host-tested) (§7)
  dashboard.py       reporting fleet, indicators, control-table rows (§7)
  review.py          staff review of disputed readings/maintenance + audit (§6, §7)
  exports.py         filtered table → .xlsx, report → .zip (§7)
  admin.py           raw back office, manager-scoped querysets
  management/commands/seed_demo.py
  tests.py
drivers/             driver cabinet
  views.py           login, session, home, protected file views
  report_views.py    four-screen report flow (§5)
  session.py ratelimit.py forms.py
staff/               staff cabinet (§7)
  views.py           dashboard, control table, VIN card, review, exports, files
  urls.py
templates/drivers/   mobile-first templates (≥360px, Russian)
templates/staff/     staff cabinet templates (dashboard, VIN card, Russian)
docs/                SPEC.md (contracts), ROADMAP.md (stages, Russian)
```

## Not yet proven on a stand

- Real phones (Chrome Android / Safari iPhone) — Stage 2 deliverable.
- PostgreSQL path exercised only via config; local runs use SQLite.
- Performance targets (§10) — not measured yet; no load to measure against.
