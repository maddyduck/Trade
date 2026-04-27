# GDPR & privacy — operator notes

> **Not legal advice.** This is a working operator's guide based on
> sensible defaults. If you're scaling beyond a few pilot trades — or
> handling sensitive data — pay a privacy lawyer £400–800 for a proper
> review.

## Personal data we collect

| Category | Where it lives | Notes |
|---|---|---|
| **Trade account data** | `trades` table | Email, phone, business name, contact name, bio, password hash. Necessary for the service. |
| **Customer booking data** | `bookings` table | Name, email, phone, postcode, address, free-text notes. Embedded on booking; no separate Customer table. |
| **Photos** | `booking_photos` + storage backend | Auto-resized JPEGs uploaded by the customer. |
| **Tracking data** | `tracking_pings` | Lat/lng/heading. **Auto-purged when session ends.** |
| **Stripe identifiers** | `bookings`, `trades` | Customer/account IDs, charge IDs, payment intent IDs. Stripe is a separate processor. |
| **Audit log** | `booking_events` | Status changes with actor + IP. |

We do **not** collect: marketing consent, location history beyond active
trips, browser fingerprints, ad identifiers, payment card details
(handled by Stripe).

## Lawful basis

- **Trade accounts**: Contract performance — the trade is using the service.
- **Customer bookings**: Contract performance — fulfilling their booking.
- **Photos**: Customer-initiated upload. Treat as part of booking data.
- **Tracking**: Legitimate interest + opt-in per trip. Trade explicitly
  ticks "Share live location."
- **SMS notifications**: Contract performance (booking confirmations,
  status updates).

We don't currently rely on consent except for the per-trip tracking
opt-in. If you add a marketing newsletter, that needs a separate consent
flow.

## Retention

Default policy in the code:

- **Active trade accounts**: kept indefinitely while account is active.
- **Bookings**: kept for 7 years (UK accounting requirement for
  financial records). Implemented manually for now — there's no
  automated purge cron yet.
- **Booking photos**: deleted with their booking when retention expires.
- **Tracking data**: auto-purged when session ends or expires (default
  60-min TTL). Inactive sessions are swept by `flask sweep-pending`.
- **Stripe webhook events**: handled/skipped events older than 60 days
  are pruned by `flask stripe-events-cleanup`.

If you want to be more aggressive, write a CLI command:
```python
@app.cli.command("purge-old-bookings")
def purge_old_bookings():
    cutoff = datetime.now(UTC) - timedelta(days=7 * 365)
    # Delete bookings older than cutoff and their attached PII.
    ...
```

## Data subject rights

The UK GDPR gives anyone whose data you hold the right to:

1. **Know what data you have** (Subject Access Request — SAR).
2. **Get a copy of it** in a portable format.
3. **Have it corrected** if it's wrong.
4. **Have it deleted** (right to erasure / "right to be forgotten").
5. **Object to processing** for marketing.

### How to handle a SAR

A customer or trade emails you saying "What data do you hold on me?"

You have **30 days** to respond.

```bash
# For a customer:
flask find-booking <their-email-or-phone>
# Compile their bookings, photos, magic-link history into a JSON or PDF.

# For a trade:
flask trade-info <email>
# Plus their bookings via dashboard export.
```

If volume warrants it, build a `flask export-data <email>` command that
dumps everything as JSON.

### How to handle a deletion request

The right to erasure isn't absolute — you can refuse if you have a
legal obligation to retain (e.g. tax records). For a trade or customer
who insists on deletion:

```bash
# Identify the records:
flask find-booking <email>

# Pseudonymise rather than hard-delete (preserves accounting):
flask shell
>>> from app.models import Booking
>>> for b in Booking.query.filter_by(customer_email='customer@example.com').all():
...     b.customer_name = 'Deleted'
...     b.customer_email = f'deleted-{b.id}@example.invalid'
...     b.customer_phone = ''
...     b.customer_address = None
...     b.job_notes = None
>>> db.session.commit()
```

The booking ref + Stripe IDs stay so the financial trail is intact. If
you have a hard-delete obligation, also delete photos:

```bash
flask shell
>>> from app.services import storage
>>> for p in booking.photos:
...     storage.delete_object(p.storage_key)
```

A `flask anonymise-customer <email>` command would automate this — write
it when you've had your first request.

## Sub-processors

Document these in your privacy policy:

- **Stripe** — payment processing. Privacy policy: stripe.com/privacy.
  Data: customer email, postcode, card details (we never see), trade
  identity verification documents.
- **Twilio** — SMS sending (if enabled). Privacy: twilio.com/legal/privacy.
  Data: customer phone, message contents.
- **Postmark / Resend / your SMTP provider** — email sending. Data:
  customer/trade email, message contents.
- **Sentry** — error tracking (if enabled). Privacy: sentry.io/privacy.
  Data: stack traces, request URLs, anonymised user IDs (we configure
  `send_default_pii=False`).
- **Cloudflare R2 / AWS S3** — photo storage. Privacy: vendor's privacy
  policy. Data: photos uploaded by customers.
- **Render** — hosting. Privacy: render.com/privacy. Data: everything.

Have data processing agreements (DPAs) on file for each of these. All
the providers above offer standard DPAs; download and store them.

## ICO registration

If you're a UK data controller, you probably need to register with the
ICO and pay a fee (£40–60/year for most small businesses).

Check at: <https://ico.org.uk/for-organisations/data-protection-fee/>

If you're handling fewer than ~250 records and you're a sole trader, you
might be exempt — but err on the side of registering. It's cheap and
gives you legal cover.

## Privacy policy template

Put a privacy policy at `/privacy` before launch. It should:

1. Identify you as the data controller (your business name + Companies
   House number).
2. List the categories of data you collect (see the table above).
3. State the lawful basis for each.
4. List sub-processors (above).
5. State retention periods.
6. Explain how to exercise data subject rights — give them an email
   address.
7. Note: customers' rights are exercised against you, not against the
   trade. Trades' rights are similarly against you.

There are decent free templates from the ICO and from
freeprivacypolicy.com. Don't rely on a template alone — make sure it
matches what the app actually does.

## Security baseline

Already in place:

- Passwords are bcrypt-hashed (no plaintext anywhere).
- Sessions are HTTP-only, SameSite=Lax, Secure in prod.
- CSRF tokens on every state-changing form.
- Magic links expire after 24 hours (configurable).
- Tracking data is auto-purged.
- Stripe webhook signatures are verified.
- Sentry is configured with `send_default_pii=False`.

Worth adding before scale:

- [ ] Rate-limiting on `/auth/login`, `/auth/forgot`, `/find` to slow
      down credential stuffing and enumeration attacks.
- [ ] 2FA on trade accounts (TOTP via `pyotp` or similar).
- [ ] Audit log access — currently `booking_events` is per-booking; you
      might want a global audit view.
- [ ] Backup strategy. Render Postgres has automatic backups but you
      should also have an off-platform backup for disaster recovery.

## What the code does well

- We don't log PII at INFO level.
- We embed customer data in bookings rather than persisting a separate
  Customer table — fewer places to go wrong on deletion.
- Tracking is opt-in per-trip, auto-expires, auto-purges.
- Photos are scoped to a single booking; deleting the booking deletes
  the photos.

## What the code doesn't do (you'll want to add)

- A "delete my account" flow for trades.
- A `/data-export` endpoint that lets a logged-in trade download their
  data as JSON.
- Automated retention enforcement (the 7-year rule isn't enforced by
  any cron).
- Customer-side data access — currently they have to email you.

These aren't blockers for a pilot launch but they are work to do before
you have real volume.
