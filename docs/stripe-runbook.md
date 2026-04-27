# Stripe ops runbook

How to handle the things that go wrong with payments.

## Principles

1. **The customer is the priority.** If there's doubt, refund.
2. **Stripe dashboard is the source of truth.** Our DB is derived state.
3. **Log everything, fix forward.** Don't delete rows; add notes.

## Daily / weekly checks

```bash
# See what Stripe events hit us recently.
flask stripe-events --limit 50

# Find stale PENDING_PAYMENT bookings. Should auto-sweep, but check.
flask sweep-pending
```

## Scenario: webhook delivered but our handler crashed mid-work

Every webhook event we see gets a `StripeEvent` row. The row starts at
`processing_result='received'` and gets flipped to `handled` / `skipped` /
`errored` when the handler finishes. If we crash *between* recording and
finishing, the row stays at 'received' forever.

Find them:

```bash
flask shell
>>> from app.models import StripeEvent
>>> from datetime import datetime, timedelta, UTC
>>> cutoff = datetime.now(UTC) - timedelta(minutes=10)
>>> stuck = StripeEvent.query.filter(
...   StripeEvent.processing_result == 'received',
...   StripeEvent.received_at < cutoff
... ).all()
>>> [(e.event_id, e.event_type, e.received_at) for e in stuck]
```

For any stuck row, look up the event in the Stripe dashboard for the full
payload, then manually reconcile the corresponding booking.

## Scenario: customer says "I paid but never got confirmed"

Find the booking and reconcile it.

```bash
flask reconcile MMK-4827-3F
```

Output shows before/after state and any actions taken. If reconcile says
`marked_paid`, you're done — confirmation email will not be re-sent
automatically (we don't want to spam), but the booking is now BOOKED and
the trade will see it.

If reconcile says `no_payment_intent_known`, the booking never started
checkout. Likely they hit the payment page, left, came back, and the draft
expired. Check Stripe dashboard by customer email.

## Scenario: webhook endpoint was down for an hour

Stripe retries for up to 3 days with exponential backoff, but don't wait.
Reconcile everything from the affected window:

```bash
flask reconcile-recent --hours 3
```

Check the output for any `errored` or `no_payment_intent_known` entries —
those need eyes.

## Scenario: a trade's Stripe account got restricted

Stripe sometimes disables `charges_enabled` on a connected account (KYC
issues, suspicious volume, etc.). Our webhook handler catches this and logs
a warning, but the trade might not know.

1. Check the trade's status:
   ```bash
   flask shell
   >>> from app.models import Trade
   >>> t = Trade.query.filter_by(slug='mckinney-plumbing').first()
   >>> t.stripe_charges_enabled
   False
   ```

2. The trade's booking page shows "bookings paused" automatically — no
   customer can start a flow.

3. Contact the trade. They need to complete whatever Stripe asked for via
   their Stripe Express dashboard. We can't resolve it for them.

4. Once resolved, the `account.updated` webhook will flip the flag back;
   or they can trigger a refresh via dashboard → Stripe → Manage.

## Scenario: refund needed but automatic refund failed

Find in `flask stripe-events` the failed event. Then issue manually from
Stripe dashboard:
1. Open the PaymentIntent (the booking's `stripe_payment_intent_id`).
2. Click Refund, choose full or partial.
3. When Stripe fires `charge.refunded`, our webhook updates
   `payment_status` automatically.

## Scenario: two bookings were paid but only one got confirmed

The paid-but-cancelled race (rare — needs cancel to complete *while*
Stripe's webhook is in flight). Our handler auto-refunds these. Check:

```bash
flask shell
>>> from app.models import Booking, BookingEvent
>>> BookingEvent.query.filter_by(event_type='payment_on_cancelled').all()
```

Any hits here, verify the refund went through (search refund in Stripe by
booking reference in metadata).

## Scenario: disputes / chargebacks

Stripe emails you when a dispute opens. Don't treat this as a bug — it's a
business event. Our destination-charges setup means:
- The platform (you) can respond to the dispute.
- If the dispute is lost, funds come out of the platform balance, and
  Stripe will attempt to debit the connected account. Trade liability.

For V1 we don't automate dispute responses. Handle case-by-case from the
Stripe dashboard.

## Testing webhooks locally

Use the Stripe CLI:

```bash
# Install (macOS): brew install stripe/stripe-cli/stripe
stripe login
stripe listen --forward-to localhost:5000/webhooks/stripe
```

The CLI prints a webhook signing secret — use that as
`STRIPE_WEBHOOK_SECRET` in your `.env`. Then trigger events:

```bash
# Full test flow
stripe trigger checkout.session.completed

# Refunds
stripe trigger charge.refunded
```

## What we deliberately don't handle yet

- **Application fees** — MVP has `PLATFORM_FEE_PCT=0`. When we start taking
  a cut, add reconciliation tests for fee amounts.
- **Multi-currency** — GBP only. The `currency` column is there but all
  code assumes pence/GBP.
- **Subscription / recurring** — bookings are one-shot. If we ever want
  retainer-style work, design properly; don't bolt onto checkout.
