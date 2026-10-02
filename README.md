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

## Current state — Stage 1 (foundation) done

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
- **Demo data**: 10 vehicles, 2 tenants, 2 managers, 1 admin, assignments and
  maintenance records covering every §6 state.
- **Django admin** as the staff back office, with manager querysets restricted to
  their own vehicles.

Stage 2 (full 4-screen report submission with photo upload), Stage 3 (staff
dashboard/indicators/exports) and Stage 4 (GPS import, deployment) are scaffolded
in the models but not yet implemented. See the roadmap.

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
- Staff back office: <http://127.0.0.1:8000/admin/> — `admin` / `Fleet!demo2026`
  (managers: `manager1`, `manager2`, same password).

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
Found 29 test(s).
System check identified no issues (0 silenced).
Ran 29 tests in 0.821s
OK
```

### Live server check (driver login flow)

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

## Project layout

```
controlfleet/        project settings, urls, wsgi/asgi
fleet/               core domain
  models.py          §9 entities
  validators.py      VIN / INN normalization and format (§4)
  maintenance.py     §6 service calculation (pure, host-tested)
  access.py          role/visibility isolation and active-pair login (§4, §7)
  admin.py           staff back office, manager-scoped querysets
  management/commands/seed_demo.py
  tests.py
drivers/             driver cabinet (login, session, screen 1)
templates/drivers/   mobile-first templates (≥360px, Russian)
docs/                SPEC.md (contracts), ROADMAP.md (stages, Russian)
```

## Not yet proven on a stand

- Real phones (Chrome Android / Safari iPhone) — Stage 2 deliverable.
- PostgreSQL path exercised only via config; local runs use SQLite.
- Performance targets (§10) — not measured yet; no load to measure against.
