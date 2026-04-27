# Sorted

A booking platform for Northern Ireland tradespeople. Each trade gets a
branded public booking page that takes a deposit via Stripe Connect to
confirm a slot. They run their day from a mobile-first dashboard.

> **Working name.** "Sorted" is a placeholder while a final name is chosen.
> Find-replace `APP_NAME` in `app/__init__.py` to rebrand.

## What's in here

- **Booking flow** — pick service → pick slot → enter details → pay
  deposit → confirmed. Magic-link customer management, no account needed.
- **Trade dashboard** — Today / Week / Invoices / More. Bookings have an
  Uber-like status stepper (Booked → On the way → Arrived → In progress
  → Complete).
- **Stripe Connect Express** — destination charges, idempotent everything,
  webhook-driven confirmation, race-safe refunds.
- **Live ETA + tracking** — when the trade taps "On the way", they pick
  an ETA and (optionally) share their location via the browser's
  geolocation API. Customer sees a marker on a map, refreshing every 30s.
  Tracking auto-ends when the trade arrives.
- **SMS via Twilio** — best-effort status texts at every state change.
  Falls back gracefully if not configured.
- **Photos** — customers upload photos of the issue, trade sees them
  before turning up. Auto-resize, thumbnails. Storage backend pluggable
  (local in dev, S3/R2 in prod).
- **Invoicing** — after a booking is complete, generate an invoice with
  line items, deduct the deposit, send a Stripe Payment Link via email.
  Auto-chase reminders at 7 / 14 / 30 days. PDF download.
- **Repeat customer detection** — returning customers get a prominent
  badge on the trade's dashboard.
- **Service templates** — new trades pick from pre-baked services
  (boiler service, leak repair, EV charger install, etc.) for quick
  onboarding.
- **Honest response times** — derived from real data (median time to
  first action across last 60 days). Hidden until there's enough data.

## Stack

- Python 3.11
- Flask 3, Jinja2, Flask-Login, Flask-WTF, Flask-Migrate
- SQLAlchemy 2.0
- PostgreSQL 16 (SQLite for tests)
- Stripe Connect Express + Checkout + Payment Links
- Twilio (SMS)
- Pillow (photo processing)
- ReportLab (invoice PDFs)
- Sentry (error tracking)
- Render.com (deployment) — or any platform with `Procfile`

## Getting started locally

```bash
# 1. Postgres (Docker)
make db-up

# 2. Python env + dependencies
make install
source .venv/bin/activate

# 3. Config
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"  # paste into SECRET_KEY

# 4. Initialise migrations on first run
flask db init
flask db migrate -m "initial schema"
flask db upgrade

# 5. Seed demo data
make seed

# 6. Run
make run
```

Visit:
- Public page: <http://localhost:5000/mckinney-plumbing>
- Trade login: <http://localhost:5000/auth/login>
  - Email: `mark@example.com`
  - Password: `sortedpassword`

## Stripe (test mode)

```bash
# 1. Sign up at https://stripe.com (free)
# 2. Keys at https://dashboard.stripe.com/test/apikeys
# 3. Update .env:
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...

# 4. Forward webhooks to local:
brew install stripe/stripe-cli/stripe  # or your platform's equivalent
stripe listen --forward-to localhost:5000/webhooks/stripe
# Copy the printed signing secret into STRIPE_WEBHOOK_SECRET

# 5. Test card: 4242 4242 4242 4242, any future date, any 3-digit CVC
```

## Project layout

```
sorted/
├── README.md                Setup, run, deploy
├── LAUNCH-CHECKLIST.md      Step-by-step from local to live
├── render.yaml              Render.com infra-as-code
├── Procfile                 Heroku/Fly fallback
├── Makefile                 Common dev shortcuts
├── pyproject.toml           Dependencies
├── config.py                Dev/Test/Prod config classes
├── wsgi.py                  Gunicorn entrypoint
├── .env.example             Every env var documented
│
├── app/
│   ├── __init__.py          Application factory
│   ├── extensions.py        db, login_manager, csrf, signer
│   ├── cli.py               flask seed-demo, sweep-pending, chase-invoices, ...
│   ├── health.py            /healthz, /readyz
│   │
│   ├── models/              SQLAlchemy ORM
│   │   ├── trade.py         Trade (the logged-in user)
│   │   ├── service.py       Service (what a trade offers)
│   │   ├── availability.py  AvailabilityRule, AvailabilityBlock
│   │   ├── booking.py       Booking + BookingEvent + status enums
│   │   ├── invoice.py       Invoice + InvoiceLineItem + status enum
│   │   ├── photo.py         BookingPhoto
│   │   ├── tracking.py      TrackingSession + TrackingPing
│   │   └── stripe_event.py  Webhook dedup log
│   │
│   ├── services/            Domain logic
│   │   ├── slots.py         Availability → bookable slots (DST-aware)
│   │   ├── bookings.py      Create, transition, cancel, refund policy
│   │   ├── invoices.py      Draft, send, mark paid, void, chase
│   │   ├── invoice_pdf.py   PDF rendering with ReportLab
│   │   ├── invoice_payments.py  Stripe payment links for invoices
│   │   ├── payments.py      Stripe Connect + checkout + reconcile
│   │   ├── tracking.py      Live ETA / location sessions
│   │   ├── storage.py       Photo storage abstraction (local + S3)
│   │   ├── customers.py     Repeat-customer detection
│   │   ├── response_time.py Honest response time computation
│   │   ├── service_templates.py  Pre-baked services for onboarding
│   │   ├── sms.py           Twilio wrapper (best-effort)
│   │   ├── email.py         Console / SMTP / memory backends
│   │   ├── magic_links.py   Signed token helpers
│   │   ├── password_reset.py Trade password reset tokens
│   │   ├── references.py    MMK-4827-3F booking ref generator
│   │   └── errors.py        Domain exceptions
│   │
│   ├── auth/                Sign in, sign up, password reset
│   ├── public/              Customer-facing routes
│   │   ├── routes.py
│   │   ├── invoice_routes.py
│   │   ├── tracking_routes.py
│   │   └── forms.py
│   ├── dashboard/           Trade-facing routes (login required)
│   │   ├── routes.py
│   │   ├── invoice_routes.py
│   │   └── forms.py + invoice_forms.py
│   ├── api/webhooks.py      Stripe webhook endpoint
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── auth/            login, signup, forgot, reset
│   │   ├── public/          trade page, booking flow, manage, tracking, invoice view
│   │   ├── dashboard/       today, week, services, profile, booking detail, invoices
│   │   ├── emails/
│   │   └── errors/
│   │
│   └── static/css/app.css   Full design system
│
├── tests/                   pytest, SQLite in-memory
├── docs/
│   └── stripe-runbook.md    Operational scenarios
└── .github/workflows/ci.yml CI on push/PR
```

## CLI reference

| Command | What it does |
|---------|---|
| `flask seed-demo` | Create the demo trade Mark + sample services |
| `flask sweep-pending` | Expire stale `PENDING_PAYMENT` bookings (run every 15min) |
| `flask reconcile <ref>` | Re-sync a single booking with Stripe |
| `flask reconcile-recent --hours 6` | Reconcile all bookings touched in the last N hours |
| `flask chase-invoices` | Send overdue invoice reminders (run daily) |
| `flask stripe-events --limit 20` | Show recent webhook events |
| `flask stripe-events-cleanup --days 60` | Trim old handled events |
| `flask find-booking <ref-or-email-or-phone>` | Look up bookings (support tool) |
| `flask issue-magic-link <ref>` | Generate a fresh magic link for a booking |
| `flask trade-info <email>` | Show a trade's setup status |

## Testing

```bash
make test         # one shot
make test-watch   # re-run on changes
```

Tests use SQLite in-memory and a memory-backend email/SMS, so no
external services are required to run them. CI runs the same suite
against Postgres on each PR.

## Deployment

See `LAUNCH-CHECKLIST.md` for the full step-by-step. The short version:

1. Push to GitHub.
2. Connect Render to the repo, point at `render.yaml`.
3. Set the secrets marked `sync: false` in Render's dashboard.
4. Add `https://<your-domain>/webhooks/stripe` in Stripe.
5. Hit `/healthz` to confirm.

## Architecture decisions worth knowing

- **Money in pence (integers), never floats.** All monetary fields are
  `*_pence`.
- **UTC in the database, Europe/London at edges.** DST matters in NI.
- **Slot generation on-the-fly**, not materialised. Cheaper, can't drift.
- **Partial unique index on `(trade_id, start_at)`** prevents
  double-booking at the DB level. Application-layer checks race; only
  the DB can't.
- **SQLAlchemy enum `values_callable` is set** so enum values match
  partial-index strings.
- **No Customer table in V1.** Customer details are embedded on each
  Booking row.
- **Destination charges via Stripe Connect**, not separate charges +
  transfers. Trade bears Stripe fees.
- **Idempotent everything.** Checkout sessions reuse via booking ref.
  Webhooks deduped via unique index on `stripe_events.event_id`.
  Refunds use idempotency keys keyed off reference + amount.
- **`mark_paid` is race-safe.** Won't un-cancel a cancelled booking.
- **Tracking is opt-in per trip and auto-purged.** Location data is
  deleted when the session ends.

## License

All rights reserved. Not for redistribution while pre-launch.
