# Petals Paradise backend (Django + DRF, deployed on Render)

Implements Phase 1 of the backend requirements: catalogue and delivery APIs, secure
customer accounts, server-priced orders, M-Pesa (Daraja STK Push) payments with verified
callbacks, customer inquiries, and a staff back office (Django admin + staff API).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DEBUG=true            # uses SQLite and a dev-only secret key
python manage.py migrate
python manage.py seed_demo   # PLACEHOLDER products, areas, fees, slots (replace before launch)
python manage.py createsuperuser   # phone number + name + password; this is your first staff account
python manage.py runserver
DEBUG=true python manage.py test tests
```

## Deploy on Render

1. Push this folder to a GitHub/GitLab repo.
2. Render dashboard > **New > Blueprint**, pick the repo. `render.yaml` creates:
   - `petals-api` web service (gunicorn, health check `/healthz`)
   - `petals-db` managed PostgreSQL (Frankfurt, the closest Render region to Kenya)
   - `petals-payment-sweeper` cron job (every 5 minutes: resolves payments whose callback
     never arrived, cancels unpaid orders, releases reserved stock)
3. Fill in the prompted secrets: `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`,
   `MPESA_SHORTCODE`, `MPESA_PASSKEY`, `MPESA_CALLBACK_BASE_URL`
   (your public API URL, e.g. `https://api.petalsparadise.co.ke`),
   `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` (your storefront origin).
4. Create the first staff user: Render dashboard > `petals-api` > **Shell**, then
   `python manage.py createsuperuser`. Staff log in at `/admin/`.
5. Check plan names and prices in `render.yaml` against Render's current pricing. Do not use
   a free database for real orders (it expires).

**Use one registrable domain for site and API** (for example `petalsparadise.co.ke` and
`api.petalsparadise.co.ke`). Session cookies are `SameSite=Lax`, so a storefront on one site
and an API on `*.onrender.com` would not send the cookie. Add the custom domain to the web
service in Render.

## Staff operations

| Task | Where |
|---|---|
| Products, variants/prices, presentations, availability, publish/unpublish/retire | `/admin/` > Catalogue |
| Delivery areas, fees, time slots, blocked dates | `/admin/` > Catalogue |
| Inquiries (status, assignment) | `/admin/` > Inquiries, or `/api/v1/admin/inquiries` |
| Order lookup and fulfilment status | `/api/v1/admin/orders`, `POST .../status` (admin shows orders read-only so every change goes through the state machine and audit trail) |
| Payments needing review (`needs_review`) | `/admin/` > Payments |
| Audit trail (append-only) | `/admin/` > Audit events |

Staff are created only in the admin or with `createsuperuser`; public sign-up can never set staff flags.

## API (all under `/api/v1`, errors use `{"error": {"code", "message", "fields"}}`)

Public: `GET products` (`?category=&search=&ordering=name|price|-price`), `GET products/{id}`,
`GET delivery-options`, `POST inquiries`, `GET auth/csrf`, `POST auth/signup`, `POST auth/login`.
Customer: `POST auth/logout`, `GET/PATCH me`, `POST orders`, `GET orders/{id}`,
`POST orders/{id}/cancel`, `GET me/orders`, `POST orders/{id}/payments/mpesa`.
Provider: `POST webhooks/mpesa/{secret}`. Staff: `admin/orders`, `admin/orders/{id}`,
`admin/orders/{id}/status`, `admin/inquiries`, `admin/inquiries/{id}`.

### What the frontend must do

- Call `GET /auth/csrf` once, then send the `csrftoken` cookie value in an `X-CSRFToken`
  header on every POST/PATCH, with `credentials: "include"`.
- Send an `Idempotency-Key` header (a UUID created once per checkout attempt) on `POST /orders`
  and `POST /orders/{id}/payments/mpesa`. Reuse the same key when retrying after a timeout.
- Send only ids and quantities in the cart (`productId`, `variantId`, `presentationId`,
  `quantity`). Prices and totals in the response come from the server; anything the browser
  sends for money is ignored.
- After starting payment, poll `GET /orders/{id}` until `payment.status` is `paid`, `failed`
  or `cancelled`. Treat only `paid` (with `order.status = confirmed`) as success.
- Product `id` is the slug. Variant `id` is numeric. Stock is reported as
  `made_to_order | in_stock | out_of_stock`, never as a fabricated count.

## M-Pesa safety rules built in

- The amount sent to Safaricom is always the server-computed order total.
- A payment is marked `paid` only when a success callback matches a payment we created,
  the amount matches, and an STK status query confirms it. Duplicate, delayed or replayed
  callbacks cannot create a second transition.
- If the callback never arrives, the status poll or cron job resolves it from the status query.
- A success that arrives after we timed out or cancelled the order is still recorded and
  flagged `needs_review` so the customer can be refunded; it is never dropped.
- The callback URL contains a secret path segment (`MPESA_CALLBACK_SECRET`). Daraja does not
  sign callbacks, so this plus the status-query check is the defence. Safaricom IP allow-listing
  can be added at a proxy if you want a further layer.
- Credentials live only in environment variables and are never logged or returned.

## Known gaps / decisions still needed (from the requirements, Phase 0)

- Placeholder seed data only: real variant labels, prices, delivery zones/fees, lead times,
  slots and blocked dates must be entered by the business.
- Password reset and phone/email verification are not implemented (policy undecided); nothing
  implies an account is verified.
- Notifications are recorded as `queued` and are never marked `sent`: no SMS/WhatsApp/email
  provider is connected yet (Phase 2).
- Refund handling is flagged (`needs_review`) but not automated.
- Guest checkout and social login are intentionally out of scope.
- Verified end to end only with mocked Daraja responses (75 automated tests). Run the sandbox
  scenarios from section 4.5 of the requirements against the real Daraja sandbox before production.
