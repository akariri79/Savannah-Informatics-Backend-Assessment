# Clinic Booking System

A REST API for a small clinic (5 doctors) to let patients see available
30-minute slots, book, cancel, and reschedule appointments.

Built with **Django + Django REST Framework**, **PostgreSQL**, deployed on
**Fly.io**, with CI/CD via **GitHub Actions**.

- **Public URL:** `https://clinic-booking-system.fly.dev` *(replace with your actual Fly.io URL after deploying)*
- **Repo:** *(add your GitHub/GitLab link here)*

---

## Section 1 — System Design

### The scenario

> 5 doctors, each with set working hours, working in 30-minute slots.
> Patients view free slots for a doctor on a date, book one, and can cancel.
> A booked slot must not be available to anyone else. The clinic wants to grow.

### Models

- **Doctor** — `name`, `specialty`, `is_active`.
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
- **Timezones:** all times are stored and returned as UTC-aware
  ISO-8601 datetimes (`USE_TZ = True`). The brief doesn't specify a clinic
  timezone, so I didn't hardcode one; a real deployment would set
  `TIME_ZONE` to the clinic's local zone.

---

## Section 2 — API

Base path: `/api/`

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/appointments` | Book a slot |
| `GET` | `/api/doctors/{id}/availability?date=YYYY-MM-DD` | List free slots for a doctor on a date |
| `PATCH` | `/api/appointments/{id}/cancel` | Cancel an appointment (`{"reason": "..."}`) |
| `PATCH` | `/api/appointments/{id}/reschedule` | Move to a new slot (`{"start_time": "..."}`) |
| `GET` | `/api/patients/{id}/appointments` | *(bonus)* upcoming appointments, sorted by date |
| `GET` | `/healthz` | Health check (used by Fly.io) |

### Example requests

```bash
# Book an appointment
curl -X POST https://clinic-booking-system.fly.dev/api/appointments \
  -H "Content-Type: application/json" \
  -d '{"doctor_id": 1, "patient_id": 1, "start_time": "2026-08-03T09:00:00Z"}'

# Check availability
curl "https://clinic-booking-system.fly.dev/api/doctors/1/availability?date=2026-08-03"

# Cancel
curl -X PATCH https://clinic-booking-system.fly.dev/api/appointments/1/cancel \
  -H "Content-Type: application/json" \
  -d '{"reason": "Patient rescheduling to a later date"}'

# Reschedule
curl -X PATCH https://clinic-booking-system.fly.dev/api/appointments/1/reschedule \
  -H "Content-Type: application/json" \
  -d '{"start_time": "2026-08-03T09:30:00Z"}'
```

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
├── fly.toml
└── .github/workflows/{ci.yml, deploy.yml}
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
git clone <this-repo>
cd clinic-booking
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
docker run -p 8000:8000 -e SECRET_KEY=dev-secret -e DATABASE_URL=<your-postgres-url> clinic-booking
```

---

## Section 3 — Deployment & CI/CD

- **Public URL:** `https://clinic-booking-system.fly.dev` *(fill in after `fly deploy`)*
- **Deploy branch:** `main`. Any push to `main` (i.e. a merged PR) triggers
  `.github/workflows/deploy.yml`, which runs `flyctl deploy --remote-only`.
- **Pipeline overview:**
  - `ci.yml` runs on every pull request (and push to `main` as a
    safety net): spins up a throwaway Postgres service container,
    installs dependencies, runs migrations, runs the full test suite.
    A PR can't be merged with a red build (once branch protection is
    turned on in the repo settings).
  - `deploy.yml` runs only on pushes to `main` (i.e. after a PR merges)
    and deploys the already-tested commit to Fly.io via `flyctl`. It
    deliberately doesn't re-run tests — that already happened in CI before
    the merge was allowed.

### One-time Fly.io setup (not part of the repeatable pipeline)

```bash
fly launch --no-deploy          # creates the app, keep the generated fly.toml or use the one in this repo
fly postgres create --name clinic-booking-db
fly postgres attach clinic-booking-db -a clinic-booking-system   # sets DATABASE_URL secret automatically
fly secrets set SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(50))")
```

Then add `FLY_API_TOKEN` (from `fly tokens create deploy`) as a GitHub
Actions repo secret so `deploy.yml` can authenticate.

---

## Section 4 — AI Reflection

1. **What I used AI for across the four sections:** scaffolding the Django
   project layout and settings (env-var-driven config, Postgres/SQLite
   fallback), drafting the initial model fields, generating boilerplate
   serializers/views/tests, writing the GitHub Actions YAML, and drafting
   this README from the design decisions I'd already made.

2. **Example where AI improved the work:** I asked it to review the
   booking flow for race conditions ("two patients hit book at the same
   time for the same slot — what breaks?"). It suggested combining
   `select_for_update()` on the doctor row with a partial unique
   constraint on `(doctor, start_time)` scoped to `status='booked'`, rather
   than relying on just an application-level check. I hadn't considered
   the DB-level partial index approach and it's a meaningfully stronger
   guarantee than app-level locking alone.

3. **Example where AI output was wrong/incomplete and how I caught it:**
   The first draft of `CheckConstraint` used the `check=` keyword
   argument, which is deprecated/removed in newer Django versions
   (`condition=` is the replacement). Running the actual migration command
   immediately surfaced a `TypeError`, which is how I caught it — I didn't
   spot it from reading the code alone, only from running it.

4. **Two decisions made without AI:**
   - **Modelling `DoctorSchedule` per-weekday instead of one start/end pair
     on `Doctor`.** This came from re-reading the scenario ("we're starting
     small but want to grow") and thinking about what breaks first as the
     clinic grows — different daily hours per doctor felt like the most
     likely near-term requirement, so I designed for it up front rather
     than bolting it on later.
   - **Keeping `Patient` separate from Django's `User` model with no auth
     layer.** This was a judgment call about scope: the brief never
     mentions login, and adding auth would have added surface area to test
     and secure without being asked for, at the cost of time better spent
     on the core booking logic the brief does specify.
