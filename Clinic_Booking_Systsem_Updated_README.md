# Clinic Booking System

A REST API for a small clinic (5 doctors) to let patients see available
30-minute slots, book, cancel, and reschedule appointments.

Built with **Django + Django REST Framework**, **PostgreSQL**, deployed on
**Back4App (Containers as a Service)**, with CI/CD via **GitHub Actions**.

- **Public URL:** `https://clinicbookingsystem-hx02lo57.b4a.run`
- **Repo:** [github.com/akariri79/Savannah-Informatics-Backend-Assessment](https://github.com/akariri79/Savannah-Informatics-Backend-Assessment)

---

## Section 1 — System Design

### The scenario

> 5 doctors, each with set working hours, working in 30-minute slots.
> Patients view free slots for a doctor on a date, book one, and can cancel.
> A booked slot must not be available to anyone else. The clinic wants to grow.

### Models

- **Doctor** — `name`, `is_active`.
- **DoctorSchedule** — one row per `(doctor, weekday)` with a `start_time`
  and `end_time`. Modelled per-weekday rather than a single start/end pair
  on `Doctor` itself, because real clinics have doctors who work different
  hours on different days (or not at all on some days — simply omit that
  weekday's row). This is one row per working day rather than one row per
  doctor, which is a bit more setup, but it avoids a bigger remodel the
  first time a doctor's Tuesday hours differ from their Monday hours.
- **Patient** — `name`, `email`, `phone`. Kept separate from Django's
  built-in `User` model since the brief doesn't mention patient login/auth,
  and coupling booking to an auth system would be scope creep for this
  assessment.
- **Appointment** — `doctor` (FK), `patient` (FK), `start_time`, `end_time`
  (both stored as timezone-aware datetimes, not just a date + slot index —
  see trade-offs below), `status` (`booked` / `cancelled`),
  `cancellation_reason`.

### Key decisions & trade-offs

1. **Slots are derived, not stored.** There's no `Slot` table pre-populated
   with every possible 30-minute block. Available slots are computed on the
   fly from `DoctorSchedule` minus existing `booked` `Appointment`s. This
   avoids a combinatorial explosion of slot rows (5 doctors × many days ×
   16 slots/day, most of which will never be touched) and keeps the "source
   of truth" for a doctor's hours in one place. The trade-off is a bit more
   computation per availability request — negligible at this scale.

2. **Double-booking is prevented at two layers, not one.**
   - *Application layer:* `select_for_update()` locks the `Doctor` row for
     the duration of the booking transaction, so two concurrent requests
     for the same doctor serialize instead of racing.
   - *Database layer:* a partial unique constraint
     (`unique_booked_doctor_slot`) on `(doctor, start_time)` where
     `status = 'booked'` means Postgres itself rejects a double-booked
     insert even if the application check is ever bypassed or the app runs
     with multiple workers/instances. Relying on just one of these felt
     risky for a system whose entire point is "that slot must not be
     available to others" — this was the constraint I most wanted to get
     right.

3. **`end_time` is derived server-side, not accepted from the client.**
   The client only sends a `start_time`; the API computes `end_time` from
   the fixed 30-minute slot duration (`SLOT_DURATION_MINUTES` in
   settings). This removes an entire class of bugs/abuse where a client
   could submit a slot of the wrong length.

4. **Cancelling frees the slot implicitly.** Since availability is always
   computed live from non-cancelled appointments, cancelling an appointment
   requires no extra bookkeeping — the slot just reappears in the next
   availability query.

5. **Reschedule = validate-as-new + move, in one transaction.** A
   reschedule runs the exact same validation a fresh booking would (working
   hours, not in the past, not already taken), just excluding the
   appointment's own current row from the "already taken" check (otherwise
   an appointment would always conflict with itself when kept on the same
   slot).

6. **Consistent error envelope.** All validation errors return
   `{"error": {"detail": "...", "fields": {...}}}` via a custom DRF
   exception handler, so client code doesn't need to special-case DRF's
   default shapes.

### Ambiguities resolved (per the assessment's instructions)

- **What "working hours" means per doctor:** resolved as configurable
  *per weekday* (see `DoctorSchedule` above) rather than one fixed daily
  window, since the brief says "set working hours" without specifying they
  are identical every day.
- **Patient identity:** the brief doesn't ask for authentication, so
  `patient_id` is passed directly in the request body rather than inferred
  from a session/token. In a production system this would sit behind auth;
  noting it here as a deliberate scope cut for this assessment.
- **Timezones:** all times are stored internally as UTC-aware ISO-8601
  datetimes (`USE_TZ = True`), with `TIME_ZONE` set to `Africa/Nairobi` —
  the clinic's local timezone — so working hours in `DoctorSchedule` and
  any locally-rendered times (e.g. in `/admin`) line up with the clinic's
  actual day, while the API still exchanges UTC over the wire.

---

## Section 2 — API

Base path: `/api/`

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | API status message (confirms the service is running) |
| `POST` | `/api/appointments` | Book a slot |
| `GET` | `/api/doctors/{id}/availability?date=YYYY-MM-DD` | List free slots for a doctor on a date |
| `PATCH` | `/api/appointments/{id}/cancel` | Cancel an appointment (`{"reason": "..."}`) |
| `PATCH` | `/api/appointments/{id}/reschedule` | Move to a new slot (`{"start_time": "..."}`) |
| `GET` | `/api/patients/{id}/appointments` | *(bonus)* upcoming appointments, sorted by date |
| `GET` | `/healthz` | Health check |

### Example requests

IDs below match the demo data created by `seed_demo_data` (doctor and
patient IDs will differ on a fresh database — check `/api/doctors/{id}/availability`
or the Django admin to confirm real IDs before running these against your
own instance).

```bash
# Book an appointment
curl -X POST https://clinicbookingsystem-hx02lo57.b4a.run/api/appointments \
  -H "Content-Type: application/json" \
  -d '{"doctor_id": 6, "patient_id": 1, "start_time": "2026-08-03T09:00:00+03:00"}'

# Check availability
curl "https://clinicbookingsystem-hx02lo57.b4a.run/api/doctors/6/availability?date=2026-08-03"

# Cancel
curl -X PATCH https://clinicbookingsystem-hx02lo57.b4a.run/api/appointments/1/cancel \
  -H "Content-Type: application/json" \
  -d '{"reason": "Patient rescheduling to a later date"}'

# Reschedule
curl -X PATCH https://clinicbookingsystem-hx02lo57.b4a.run/api/appointments/1/reschedule \
  -H "Content-Type: application/json" \
  -d '{"start_time": "2026-08-03T09:30:00+03:00"}'
```

Note the explicit `+03:00` offset on `start_time` — the API stores and
compares times as UTC internally, so a bare `Z` (UTC) timestamp books the
slot at that literal UTC instant, which is 3 hours off from the same wall-clock
time in the clinic's `Africa/Nairobi` zone. Sending the offset explicitly
books the slot patients actually see rendered in local time.

### Validation & error handling

- All validation failures return `400` with the `{"error": {...}}` shape
  described above.
- Booking outside working hours, in the past, within the 1-hour lead-time
  window, on a misaligned (non-30-minute-boundary) time, or on an
  already-booked slot all return `400` with a specific message.
- Cancelling an already-cancelled appointment, or rescheduling a cancelled
  appointment, returns `400`.
- A missing doctor/patient/appointment id returns `404`.

### Code structure

```
clinic-booking/
├── config/           # Django project settings, root URLs, WSGI
├── clinic/
│   ├── models.py      # Doctor, DoctorSchedule, Patient, Appointment
│   ├── services.py    # Slot generation + booking validation (business logic)
│   ├── serializers.py # Request/response shaping + validation wiring
│   ├── views.py        # Thin views - delegate to services/serializers
│   ├── exceptions.py   # Custom DRF exception handler (consistent error shape)
│   ├── urls.py
│   ├── admin.py
│   ├── management/commands/seed_demo_data.py
│   └── tests/
│       ├── test_services.py   # Unit tests for slot generation & validation
│       └── test_api.py         # Integration tests for every endpoint
├── Dockerfile
└── .github/workflows/ci.yml
```

Business logic lives in `services.py`, independent of DRF, so it's directly
unit-testable and reusable (the availability endpoint and the booking
validator both call `get_slots_for_day`).

### Tests

22 tests covering slot generation, every validation rule, and every
endpoint (happy path + error cases).

```bash
python manage.py test clinic --verbosity 2
```

---

## Running locally

**Requirements:** Python 3.12, PostgreSQL (optional — SQLite is used
automatically if `DATABASE_URL` isn't set).

```bash
git clone https://github.com/akariri79/Savannah-Informatics-Backend-Assessment.git
cd Savannah-Informatics-Backend-Assessment
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit if you want Postgres instead of SQLite

python manage.py migrate
python manage.py seed_demo_data   # creates 5 doctors (Mon-Fri, 09:00-17:00) + 1 demo patient
python manage.py createsuperuser  # optional, for /admin

python manage.py runserver
```

API is now at `http://localhost:8000/api/`. Django admin at `/admin/`.

### Running with Docker

```bash
docker build -t clinic-booking .
docker run -p 8000:8000 -e DJANGO_SECRET_KEY=dev-secret -e DATABASE_URL=<your-postgres-url> clinic-booking
```

---

## Section 3 — Deployment & CI/CD

- **Host:** [Back4App](https://www.back4app.com/) Containers as a Service,
  running the app from the `Dockerfile` in this repo (not a buildpack —
  Back4App builds and runs the image directly).
- **Public URL:** `https://clinicbookingsystem-hx02lo57.b4a.run` — confirmed
  live.
- **Database:** Postgres hosted on [Neon](https://neon.tech/), supplied to
  the app as a `DATABASE_URL` secret. Back4App's own database add-on isn't
  used — the app reads the connection string from the environment, so any
  Postgres instance works, and Neon is what this deployment actually
  points at.
- **Two independent pipelines, not one.** GitHub Actions handles testing
  only; it has no deploy step and never talks to Back4App. Deployment is
  handled entirely on Back4App's side via its own git integration, which
  watches this repo and builds/runs the `Dockerfile` whenever it sees a
  new commit on `main`.
- **CI (`.github/workflows/ci.yml`):** runs on every pull request and on
  every push to `main`.
  1. Checks out the code and spins up a throwaway Postgres service
     container for the test database.
  2. Installs dependencies, runs migrations, runs the full test suite.
  3. That's it — no build, no deploy, no artifact is produced.
- **Verified working.** A test PR against `main` confirmed the `test` job
  triggers correctly on `pull_request`, the Postgres service container
  comes up healthy, and the full suite passes in ~34s in the CI
  environment. Branch protection on `main` now requires the `test` check
  to pass before a PR can merge — a red run blocks the merge outright,
  it isn't just informational.
- **CD (Back4App, not GitHub Actions):** Back4App is configured to watch
  the `main` branch of this repo. On a new push, it builds the image from
  the repo's `Dockerfile` and redeploys the container automatically at
  `https://clinicbookingsystem-hx02lo57.b4a.run`. GitHub Actions has no
  role in this step and holds no Back4App credentials — deployment is
  entirely Back4App's own git integration, independent of `ci.yml`.
  Because branch protection now requires CI to pass before merging, and
  Back4App only redeploys on pushes to `main`, in practice a broken build
  can no longer reach `main` through the normal PR flow — and therefore
  can't reach production either. The one gap that remains: a direct push
  to `main` (bypassing a PR) would still trigger a Back4App deploy without
  ever running `ci.yml`, since branch protection only governs merges, not
  direct pushes. If that matters, add "Restrict who can push to matching
  branches" to the same protection rule.
- **HTTPS behind Back4App's CloudFront layer.** Back4App fronts every
  container with CloudFront, which terminates TLS at the edge and forwards
  requests to the container over plain HTTP internally. Django's
  `SECURE_SSL_REDIRECT` — which force-redirects any non-HTTPS request to
  HTTPS — has to be `False` here, not `True`: CloudFront already refuses
  to forward anything but HTTPS to the origin, so Django re-checking and
  redirecting on top of that produced a redirect loop (`301` to the exact
  same HTTPS URL) rather than adding any real protection. The other
  production-hardening settings (`SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE`, HSTS) stay on; only the redundant redirect is
  disabled.

### One-time Back4App setup (not part of the repeatable pipeline)

1. Create a new **Containers as a Service** app on Back4App and connect
   it directly to this repository, watching the `main` branch. Back4App
   builds and deploys from the repo's `Dockerfile` itself — GitHub Actions
   is not involved in this step and holds no Back4App credentials.
2. Set the required environment variables/secrets in the Back4App app
   dashboard: `DJANGO_SECRET_KEY` (must match this exact name — the app
   reads it via `os.environ.get('DJANGO_SECRET_KEY', ...)`, and a
   differently-named variable such as `SECRET_KEY` will silently fall
   back to an insecure placeholder instead of raising an error),
   `DEBUG=False`, `ALLOWED_HOSTS` (the Back4App-assigned domain), and
   `DATABASE_URL` (pointing at the Neon Postgres instance).
3. Set the container **Port** to `8000` — this must match what the
   `Dockerfile` exposes and what gunicorn binds to
   (`--bind 0.0.0.0:8000`); Back4App's default placeholder port does not
   match this app's port and will cause health checks to fail if left
   unchanged.
4. ✅ Branch protection on `main` is configured: **Require status checks
   to pass before merging** is enabled with the `test` check (GitHub
   Actions) selected as required. This is what prevents a failing PR from
   reaching `main` — and, by extension, from reaching Back4App.

---

## Section 4 — AI Reflection

1. **What I used AI for across the four sections:** scaffolding the Django
   project layout and settings (env-var-driven config, Postgres/SQLite
   fallback), drafting the initial model fields, generating boilerplate
   serializers/views/tests, writing the GitHub Actions YAML, reviewing my
   `Dockerfile` and settings against Back4App's specific deployment
   environment (CloudFront proxy behavior, port configuration), and
   drafting this README from the design decisions I'd already made.

2. **Example where AI improved the work:** I asked it to review the
   booking flow for race conditions ("two patients hit book at the same
   time for the same slot — what breaks?"). It suggested combining
   `select_for_update()` on the doctor row with a partial unique
   constraint on `(doctor, start_time)` scoped to `status='booked'`, rather
   than relying on just an application-level check. I hadn't considered
   the DB-level partial index approach and it's a meaningfully stronger
   guarantee than app-level locking alone.

3. **Two examples where AI output was wrong or incomplete, and how I
   caught each one:**
   - The first draft of a model field, `Doctor.is_active`, ended up typed
     as `CharField` instead of `BooleanField(default=True)` at some point
     during iteration. This didn't throw an error anywhere obvious — Django
     happily created rows with `is_active=""`, and every doctor silently
     failed the `is_active=True` filter used by both the availability
     endpoint and the booking serializer, returning `404`s that looked
     like a missing-data problem rather than a type bug. I only found the
     real cause by dropping into a shell and checking `repr(doctor.is_active)`
     directly rather than trusting what the field looked like it should
     return — a good reminder that a field "looking right" in a model
     definition doesn't guarantee it behaves right at runtime.
   - `CheckConstraint` was first generated using the `check=` keyword
     argument, which is deprecated/removed in newer Django versions
     (`condition=` is the replacement). Running the actual migration
     command immediately surfaced a `TypeError`, which is how I caught
     it — I didn't spot it from reading the code alone, only from running
     it.

4. **Two decisions made without AI:**
   - **Modelling `DoctorSchedule` per-weekday instead of one start/end pair
     on `Doctor`.** This came from re-reading the scenario ("we're starting
     small but want to grow") and thinking about what breaks first as the
     clinic grows, different daily hours per doctor felt like the most
     likely near-term requirement, so I designed for it up front rather
     than bolting it on later.
   - **Keeping `Patient` separate from Django's `User` model with no auth
     layer.** This was a judgment call about scope: the brief never
     mentions login, and adding auth would have added surface area to test
     and secure without being asked for, at the cost of time better spent
     on the core booking logic the brief does specify.
