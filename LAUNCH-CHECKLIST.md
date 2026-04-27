# Launch checklist — Sorted

Use this to take Sorted from "running on your laptop" to "real customers
booking real trades." The order matters: each step builds on the previous.

---

## 1. Brand & legal foundations

- [ ] **Pick a final name** (you flagged this is being decided separately).
      Find-replace `APP_NAME` in `app/__init__.py`. Update the WhatsApp
      share text and footer in templates.
- [ ] **Buy the domain.** `.co.uk` and `.app` are the safe bets; `.ni`
      isn't publicly available.
- [ ] **Register the company at Companies House.** £50, ~24 hours.
- [ ] **Open a business bank account.** Starling, Tide, Monzo Business —
      all do free tiers. Stripe payouts go here.
- [ ] **Trademark the name** if you can. £170 for one class, £200 for the
      "Right Start" service that gives you a refundable opinion. Class 9
      (downloadable apps) and Class 42 (SaaS) are the relevant ones.
- [ ] **Buy domain SSL** — Render handles this automatically once DNS
      points at them.
- [ ] **Update privacy policy and terms** in templates/legal/. The
      versions in this repo are sensible defaults; have them reviewed if
      you're scaling beyond a few pilot trades.

## 2. Stripe Connect — start this early, takes time

- [ ] **Create a Stripe account** at stripe.com.
- [ ] **Apply for Stripe Connect** at
      https://dashboard.stripe.com/settings/connect. Approval takes 2–10
      business days. You'll need answers to:
   - What's your business model? ("SaaS marketplace — we connect
     tradespeople with customers and take deposits on their behalf.")
   - Who's your end customer? (Tradespeople / their customers.)
   - Estimated monthly volume? (Be honest — start small.)
- [ ] **Set up your platform branding** in the Stripe dashboard. This
      shows on Stripe-hosted onboarding pages.
- [ ] **Test mode first.** Get the whole booking flow working end-to-end
      with test cards (`4242 4242 4242 4242`).
- [ ] **Switch to live mode.** Replace `pk_test_*` and `sk_test_*` with
      `pk_live_*` and `sk_live_*`. Update webhook endpoint.
- [ ] **Add the live webhook** in Stripe → Developers → Webhooks:
      `https://your-domain.com/webhooks/stripe`. Subscribe to:
      `checkout.session.completed`, `payment_intent.payment_failed`,
      `charge.refunded`, `account.updated`.
      Copy the signing secret to `STRIPE_WEBHOOK_SECRET`.

## 3. Hosting (Render)

- [ ] **Create a Render account.**
- [ ] **Push the repo to GitHub.** Connect the GitHub repo to Render.
- [ ] **Point Render at `render.yaml`** — it'll provision the web
      service, Postgres database, and cron job in one click.
- [ ] **Set the secrets** in Render dashboard for variables marked
      `sync: false` in `render.yaml`:
      `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`,
      `STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_CLIENT_ID`,
      `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`,
      `SENTRY_DSN` (optional but recommended),
      `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
      (when SMS is enabled).
- [ ] **Update `APP_BASE_URL`** to your real domain.
- [ ] **Point your domain at Render.** Add an A or CNAME record per
      Render's instructions.
- [ ] **Run migrations on first deploy** — already in the start command,
      but verify it works on the deploy log.
- [ ] **Hit `/healthz`** in your browser to confirm the app is up.

## 4. Email — required for booking confirmations

- [ ] **Pick a provider.** Postmark (£10/mo), Resend (free tier), AWS
      SES (cheapest at scale, requires verification dance).
- [ ] **Verify your sending domain.** Add SPF, DKIM, DMARC records to
      DNS. Postmark/Resend walk you through this.
- [ ] **Set `MAIL_BACKEND=smtp`, `MAIL_FROM=Sorted <bookings@yourdomain.com>`,**
      plus the SMTP credentials.
- [ ] **Send a real test email.** Trigger via the seed script:
      `flask seed-demo && book a fake job through the UI`.
- [ ] **Check it doesn't go to spam.** Send to your own Gmail and verify
      it lands in inbox, not promotions.

## 5. SMS (optional but high-value)

- [ ] **Buy a Twilio UK number** (~£8/mo). Make sure it's an SMS-enabled
      number (UK mobile-style numbers are best for trust).
- [ ] **Set `TWILIO_ENABLED=true`** + the three credentials in env.
- [ ] **Test it** — book a fake job, advance through statuses, verify a
      text arrives at your own phone.
- [ ] **Note the cost.** Each SMS is roughly 4p in the UK. At 50
      bookings/month with 3 status texts each, that's ~£6/mo. Not a lot
      but track it.

## 6. Photo storage (production)

- [ ] **Choose a provider.** Cloudflare R2 (recommended — no egress
      fees, generous free tier) or AWS S3.
- [ ] **Create the bucket.** For R2: `sorted-photos`. Make it public-read
      (R2 has a one-click "public bucket" toggle).
- [ ] **Set up public URL.** R2 gives you `pub-xxx.r2.dev` for free; you
      can attach a custom subdomain like `photos.yourdomain.com`.
- [ ] **Set env vars:** `STORAGE_BACKEND=s3`,
      `STORAGE_S3_BUCKET=sorted-photos`,
      `STORAGE_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com`,
      `STORAGE_S3_PUBLIC_BASE=https://photos.yourdomain.com`,
      `STORAGE_S3_ACCESS_KEY=...`, `STORAGE_S3_SECRET_KEY=...`.
- [ ] **Test it** — upload a photo through the manage-booking flow,
      verify it appears in the bucket and is reachable via the public URL.

## 7. Error tracking (recommended)

- [ ] **Sign up for Sentry** (free tier covers 5k events/month).
- [ ] **Set `SENTRY_DSN`** and `ENV_NAME=production`.
- [ ] **Trigger a test error** to verify it lands. Visit `/sentry-debug`
      (you'll need to add this temporarily) or rely on a real first
      production incident.

## 8. Operational cron jobs

The `render.yaml` includes one cron (`flask sweep-pending`). Add these
manually if you want them — each is a separate cron service on Render
($7/mo each, so weigh the value).

- [ ] **`flask chase-invoices`** — daily at 9am. Sends overdue invoice
      reminders. High value if you're using invoicing.
- [ ] **`flask reconcile-recent --hours 6`** — every 6 hours. Catches
      bookings stuck in `pending_payment` due to webhook delays.
- [ ] **`flask stripe-events-cleanup --days 60`** — weekly. Trims old
      webhook event log.

If you don't want to pay for multiple cron services, run all of these
under one cron via a small shell script or a Python entrypoint that
fans them out.

## 9. Pre-launch dry run

- [ ] **Sign up as a new trade** through the live signup flow.
- [ ] **Connect Stripe** (real Stripe Connect onboarding, not test mode).
- [ ] **Add 2–3 services.**
- [ ] **Set availability for the next 7 days.**
- [ ] **Publish your booking page.**
- [ ] **Book yourself a job** as a customer using a real card (or a £1
      test service so you can verify the money flow).
- [ ] **Watch the money arrive in your Stripe dashboard.** Verify it's
      routed to the trade's connected account, not the platform.
- [ ] **Advance the booking through every status.** Confirm SMS + emails
      go out.
- [ ] **Test the live tracking link** on your phone. Walk around outside
      and watch the marker move.
- [ ] **Mark complete and create an invoice.** Send to yourself, pay it
      via the Stripe payment link, verify everything closes out.
- [ ] **Cancel a different booking** and confirm the refund hits the
      original card.

## 10. Pilot trades — humans, not just code

- [ ] **Pick 2–3 NI plumbers/heating engineers you actually know.** A
      relative, a friend's referral, the local plumber who's done your
      boiler.
- [ ] **Don't sell — show.** Pull up the booking flow on your phone, let
      them tap through it. Watch their face.
- [ ] **Offer to set them up for free** for 60 days. No charge, no card.
      In exchange, you get their feedback every fortnight.
- [ ] **Set up their first service yourself.** Don't make them do form
      filling on day one. You're proving value, not testing UX.
- [ ] **Share their booking link in a WhatsApp message** to one or two
      previous customers each. The trade's existing customer base is the
      cheapest way to get the first real bookings.

## 11. Marketing site (separate codebase)

- [ ] **Build a simple landing page.** Not in this codebase — a separate
      static site (Astro, Eleventy, or just hand-coded HTML on a CDN).
- [ ] **Pages needed:** home (positioning, screenshots, social proof),
      pricing, "for tradespeople" (signup CTA), "how it works" for
      customers.
- [ ] **Domain pointing.** Either `yourdomain.com` is the marketing site
      and `app.yourdomain.com` is the app, or use one domain with a
      smart router. The render.yaml currently assumes the app is at root.

## 12. After-launch ops cadence

- [ ] **Daily** (first month): check Sentry, check Stripe dashboard for
      failed payments, check `flask trade-info <email>` for any flagged
      onboarding issues.
- [ ] **Weekly**: review pilot trade feedback. One question to each:
      "What's the most annoying thing about using Sorted this week?"
- [ ] **Monthly**: Stripe reconciliation — does the money in your bank
      match the bookings in the app?
- [ ] **Quarterly**: review GDPR data retention. Old cancelled bookings
      can be purged. Check the runbook in `docs/`.

---

When you've ticked everything in sections 1–9, you're ready to invite
your first trade.

When you've done section 10 with at least one trade and they've
processed at least one real booking, you're "live."

When you've done section 11 and started running ads, you're "launched."

The leap that matters most is the one in section 10. Don't skip it.
