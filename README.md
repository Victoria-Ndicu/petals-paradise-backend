# Petals Paradise backend (Django + DRF, deployed on Render)

Implements Phase 1 of the backend requirements: catalogue and delivery APIs, secure
customer accounts (password + Google/Apple), server-priced orders, customer inquiries, and a
staff back office (Django admin + staff API).

**Payments are not part of this repo.** They will live in a separate repository/service. Until
then orders are created as `pending_payment` and staff confirm them manually (see "Payments
integration seam" below).

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

On Windows PowerShell, activate the virtual environment and set the local
development environment variables in the same terminal before running Django:

```powershell
.\venv\Scripts\Activate.ps1
$env:DEBUG = "true"
$env:SECRET_KEY = "local-development-only-do-not-use-in-production"
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

`DEBUG=true` enables the local SQLite fallback and development-only secret key.
Do not use these values in production; production requires a configured
`SECRET_KEY` and persistent `DATABASE_URL`.

### Neon, Buckets, CDN, and Brevo catalogue setup

- For Neon, configure `DATABASE_URL` with the pooled connection string for app traffic.
  Keep `conn_health_checks`, disable server-side cursors, and use the direct connection string
  as `MIGRATION_DATABASE_URL` for deploy-time migrations. The build uses the direct URL for
  migrations and cache-table creation; the running service continues using `DATABASE_URL`.
- Product images can be uploaded in Django admin. Configure `STORAGE_BUCKET`,
  `STORAGE_ENDPOINT_URL`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`, and
  `STORAGE_PUBLIC_BASE` to enable Neon Buckets storage; they are required outside `DEBUG` so
  production uploads are not silently stored on ephemeral local disk. `STORAGE_REGION` defaults to
  `us-east-2`; set it to the region shown in the Neon console. The S3 client uses path-style
  addressing and SigV4. Local development uses Django's filesystem storage. Existing `image_url`
  values remain supported. Set `STORAGE_PUBLIC_BASE` to the bucket's public base URL, including
  its bucket path if Neon requires it.
  Apply the image field migration with `python manage.py migrate` (it runs automatically in the
  Render build).
- To use an image already uploaded directly to a Neon Bucket, copy its public URL and paste it
  into the product's **Image url** field in Django admin. The URL is `https://` plus
  `STORAGE_PUBLIC_BASE` plus the object's key, for example
  `https://branch.storage.c-2.us-east-2.aws.neon.tech/flowers/peony.jpg`. Leave the **Image**
  upload field empty; uploaded files take precedence if both image fields are set. Add **Image
  alt** text for accessibility.
- Create an active category before creating a product. Add the name, description, image URL,
  image alt text, and stock policy; add one or more active price variants in the inline variant
  table (prices are whole KES). Save as **Draft**, then select the product in the product list
  and choose **Publish selected products**. Only published products appear in the public API;
  publishing also triggers the product's Brevo sync after the database transaction commits.
- Public product and delivery-option responses include `Cache-Control` headers for short-lived
  CDN caching. If you use a CDN, configure it to respect these origin headers and cache
  `/api/v1/products*` and `/api/v1/delivery-options`. Product availability may be cached for up
  to 60 seconds; checkout still validates stock and prices against the database.
- Set `BREVO_API_KEY` and `SITE_URL` to enable one-way product sync after catalogue saves and
  publish/unpublish/retire actions. Sync failures are logged and do not block admin saves. Existing
products are not automatically backfilled. Run this once from `python manage.py shell` to sync
published products:

```python
from apps.catalogue.brevo import sync_product
from apps.catalogue.models import Product

for product in Product.objects.filter(status=Product.Status.PUBLISHED).iterator():
    sync_product(product)
```

### New-order alerts

The backend sends email after a new order transaction commits. Admins receive an order alert at
the comma-separated recipients in `ORDER_ALERT_EMAILS`. Customers receive a separate confirmation
at the email on their account, when one is set. The customer message says the order was received
and is awaiting payment confirmation; it does not claim that payment was received. Messages are
sent in a background thread so email delivery does not hold up checkout. An idempotent retry does
not send duplicate messages.

Configure `ORDER_ALERT_EMAILS` and the existing SMTP settings (`EMAIL_HOST`, `EMAIL_PORT`,
`EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL`) on the API
service in Render. For Brevo SMTP, use the host, login, and SMTP key shown in your Brevo SMTP
settings, and verify the sender address. Gmail testing requires an app password, not the account
password. To test SMTP locally, set the same environment variables and run:

```powershell
python manage.py shell -c "from django.core.mail import send_mail; send_mail('Test', 'Hello', None, ['you@example.com'])"
```

## Deploy on Render

1. Push this folder to a GitHub/GitLab repo.
2. Render dashboard > **New > Blueprint**, pick the repo. `render.yaml` creates:
   - `petals-api` web service (gunicorn, health check `/healthz`)
   - `petals-db` managed PostgreSQL (Frankfurt, the closest Render region to Kenya)
   - `petals-order-sweeper` cron job (every 5 minutes: cancels orders still awaiting payment
     past their deadline and releases reserved stock)
3. Fill in the prompted values: `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` (your
   storefront origin), `STORAGE_*` values for persistent product images, and `GOOGLE_CLIENT_IDS` /
   `APPLE_CLIENT_IDS` if you want social sign-in. Configure SMTP and Brevo values if using those
   features. The Blueprint connects `DATABASE_URL` to the `petals-db` Postgres database and applies
   migrations during the build. If you already created the web service outside this Blueprint,
   configure its `DATABASE_URL` to use the `petals-db` connection string; production now refuses
   to start against SQLite.
   The current Blueprint still selects Render Postgres by default. To move an existing deployment
   to Neon, copy and verify the database first, then configure the web and cron services to use
   Neon's pooled URL as `DATABASE_URL`, and set the web service's `MIGRATION_DATABASE_URL` to
   Neon's direct URL. Keep the old database until the cutover is verified.
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
| Order lookup, manual payment confirmation and fulfilment status | `/api/v1/admin/orders`, `POST .../status` (admin shows orders read-only so every change goes through the state machine and audit trail) |
| Audit trail (append-only) | `/admin/` > Audit events |

Staff are created only in the admin or with `createsuperuser`; public sign-up can never set staff flags.

## API (all under `/api/v1`, errors use `{"error": {"code", "message", "fields"}}`)

Public: `GET products` (`?category=&search=&ordering=name|price|-price`), `GET products/{id}`,
`GET delivery-options`, `POST inquiries`, `GET auth/csrf`, `POST auth/signup`, `POST auth/login`,
`POST auth/verify-email`, `POST auth/verify-email/resend`, `POST auth/social/google`,
`POST auth/social/apple`.
Customer: `POST auth/logout`, `POST auth/social/{provider}/link`, `GET/PATCH me`, `POST orders`, `GET orders/{id}`,
`POST orders/{id}/cancel`, `GET me/orders`. Staff: `admin/orders`, `admin/orders/{id}`,
`admin/orders/{id}/status`, `admin/inquiries`, `admin/inquiries/{id}`.

Password sign-up requires an email and password; phone is optional and can be added later via
`PATCH /me`. A verification link is sent after sign-up and when the account email changes.
Phone login remains available for accounts that have a phone. Checkout still asks for the
delivery recipient's phone number.

### What the frontend must do

- Call `GET /auth/csrf` once, then send the `csrftoken` cookie value in an `X-CSRFToken`
  header on every POST/PATCH, with `credentials: "include"`.
- Send an `Idempotency-Key` header (a UUID created once per checkout attempt) on `POST /orders`
  Reuse the same key when retrying after a timeout.
- Send only ids and quantities in the cart (`productId`, `variantId`, `presentationId`,
  `quantity`). Prices and totals in the response come from the server; anything the browser
  sends for money is ignored.
- A new order has `status = pending_payment`. Treat an order as paid only when
  `status = confirmed` (or later). Poll `GET /orders/{id}` to see it change.
- Product `id` is the slug. Variant `id` is numeric. Stock is reported as
  `made_to_order | in_stock | out_of_stock`, never as a fabricated count.

## Payments integration seam

Payment code (M-Pesa/Daraja) was removed from this repo on purpose. What remains is
payment-agnostic:

- Orders start as `pending_payment` and carry `totalKes` (server-computed) and `reference`.
- `apps.orders.services.confirm_order_payment(order_id, actor=None, note=...)` is the single
  function that means "this order is paid". It is idempotent, moves the order to `confirmed`,
  and queues the confirmation notifications. It returns `"confirmed"`, `"already"` or
  `"cancelled"` (the order was cancelled first: the payments service must handle the refund).
  Staff use it today via `POST /api/v1/admin/orders/{id}/status {"status": "confirmed"}`.
- Orders still awaiting payment are cancelled and their reserved stock released after
  `PAYMENT_TIMEOUT_MINUTES` (default 1440 = 24h, because staff confirm manually for now).
  Shorten it when online payment is connected.
- Customers cannot confirm their own orders, and staff cannot fulfil an unpaid order.

When the payments repo is ready, decide whether it runs as its own service (calls a
server-to-server endpoint you add here) or is added back as a Django app that calls
`confirm_order_payment`. The order's payer phone is no longer collected at order time; the
payment step should ask for it.

## Sign in with Google / Apple

Flow: the browser gets an **ID token** from Google Identity Services / Sign in with Apple and
posts it to the backend, which verifies signature, issuer, audience and expiry against the
provider's public keys before creating the session cookie. Nothing the browser says about who
the user is is trusted.

```
POST /api/v1/auth/social/google   {"idToken": "...", "nonce": "...", "phone": "0712345678"}
POST /api/v1/auth/social/apple    {"idToken": "...", "name": "Jane Doe", "phone": "0712345678"}
```

- Returning user -> `200` and a session. New user -> phone is optional; the account is created
  with `phone: null` if it is omitted. Success is `201`.
- If the provider supplies a verified email already used by an account, the social identity is
  linked to that account and the customer is signed in. An unverified matching email is never
  linked; the API returns `409 EMAIL_NOT_VERIFIED`.
- Apple only gives the name to the browser (not in the token), so pass it as `name`.
- `nonce` is optional but recommended: send the same value you gave the provider.
- For password-created accounts, email starts unverified until the signed verification link is
  used. Google/Apple account linking by email is allowed only when the provider's verified-email
  claim is valid. Existing accounts can also link a provider while signed in using
  `POST auth/social/{provider}/link`.
- Social-only accounts have no usable password. `GET me` returns `socialProviders`.
- Disabled until configured: set `GOOGLE_CLIENT_IDS` (Web client ID from Google Cloud Console >
  APIs & Services > Credentials, with your storefront as an authorised JavaScript origin) and/or
  `APPLE_CLIENT_IDS` (Services ID from Apple Developer > Identifiers, with your domain and
  return URL registered). Unset -> `503 PROVIDER_NOT_CONFIGURED`.
- Apple "Hide My Email" addresses (`@privaterelay.appleid.com`) are stored as the email as given.
- Not built: unlinking a provider and setting a password on a social-only account (needs the
  password-reset policy first, so users can't lock themselves out).

### Email delivery configuration

Email verification uses Django SMTP. Configure `FRONTEND_URL` to the storefront origin and set
`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, and
`DEFAULT_FROM_EMAIL` in Render. For local `DEBUG=true` development without an SMTP host, messages
are printed to the runserver console. For production, use an SMTP provider with a free tier for
testing or low-volume transactional mail; never put SMTP credentials in source control. For
example, Gmail SMTP uses `smtp.gmail.com:587` with TLS and an app password (not the Google
account password); check the provider's current sending limits and sender requirements.

## Known gaps / decisions still needed (from the requirements, Phase 0)

- Placeholder seed data only: real variant labels, prices, delivery zones/fees, lead times,
  slots and blocked dates must be entered by the business.
- Password reset is not implemented. Email verification links expire after 24 hours.
- Notifications are recorded as `queued` and are never marked `sent`: no SMS/WhatsApp/email
  provider is connected yet (Phase 2).
- No online payments yet and no refund handling (separate payments repo).
- Guest checkout is intentionally out of scope.
- 71 automated tests run on SQLite. Run them against Postgres in CI before launch.
