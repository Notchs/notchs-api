# notchs-api
This is the backend code of Notchs web application.

Built with **FastAPI**, **SQLAlchemy 2.0 (async)**, **PostgreSQL** and **Alembic**.

## Project structure

```
notchs-api/
├── app/
│   ├── main.py                 # App factory: middleware, exception handlers, routers
│   ├── cli.py                  # python -m app.cli: seed, create-admin, admin-link
│   ├── api/
│   │   └── router.py           # Registers every module router under /api/v1
│   ├── core/                   # Cross-cutting concerns
│   │   ├── config.py           # Settings (env / .env)
│   │   ├── logging.py          # Logging setup
│   │   ├── redis.py            # Async Redis client + connection check
│   │   ├── rate_limit.py       # Fixed-window rate limits in Redis
│   │   └── exception_handlers.py # Turns exceptions into the error JSON response
│   ├── db/
│   │   ├── base.py             # Declarative Base, mixins, `Entity` base model
│   │   ├── session.py          # Async engine + per-request session
│   │   ├── models.py           # Model registry for Alembic autogenerate
│   │   └── seed.py             # Idempotent seed data (cities, areas)
│   ├── common/                 # Shared, reusable building blocks (DRY)
│   │   ├── exceptions.py       # AppException + BadRequest/NotFound/Conflict/... to raise and subclass
│   │   ├── crypto.py           # Reversible encryption (Fernet), e.g. TOTP secrets
│   │   ├── http.py             # Request helpers: client IP, user agent
│   │   ├── phone.py            # Phone numbers to E.164 (+919876543210)
│   │   ├── schemas.py          # BaseSchema, TimestampSchema, EntitySchema, CustomResponse, PaginatedResponse, ErrorResponse
│   │   ├── dependencies.py     # DBSession, RedisClient, Pagination
│   │   ├── repository.py       # Generic async CRUD repository
│   │   └── service.py          # Generic CRUD service (get/list/create/update/delete)
│   ├── clients/                # External services: email (Resend), CAPTCHA (Turnstile), WhatsApp (Meta)
│   ├── middleware/
│   │   └── request_context.py  # X-Request-ID + request timing logs
│   └── modules/                # One package per feature
│       ├── admin_users/        # Notchs staff accounts (model only; login comes with auth)
│       ├── auth/               # Sessions, venue + admin login, passwords, email codes
│       ├── dropdowns/          # GET /dropdowns/{name}: option lists for every form
│       ├── health/
│       ├── locations/          # Cities and areas (models only)
│       ├── leaderboards/       # City ranks in Redis, rebuilt from the points ledger
│       ├── tags/               # Tags (admins only), venue tags and items, "What did you try?"
│       ├── users/              # Customers (model only)
│       ├── venue_applications/ # "Apply as a venue" form + admin review (approve/reject)
│       ├── venues/             # Venues, venue members, counter phone pairing and display
│       └── whatsapp/           # WhatsApp: messages table, sender, webhook, processing
├── alembic/                    # Database migrations
├── tests/                      # Mirrors app/ layout
├── .devcontainer/              # VS Code Dev Container config
├── .env.example
├── Dockerfile                  # Multi-stage: dev / prod
├── docker-compose.yml          # Local stack: api + redis (+ optional local postgres)
├── requirements.txt / requirements-dev.txt
└── pyproject.toml              # Ruff + pytest config
```

## Module convention

Every feature lives in `app/modules/<name>/` with the same layers:

| File              | Responsibility                                                        |
|-------------------|-----------------------------------------------------------------------|
| `models.py`       | SQLAlchemy models — subclass `app.db.base.Entity`                     |
| `schemas.py`      | Pydantic request/response schemas — subclass `BaseSchema`/`EntitySchema` |
| `repository.py`   | Data access — subclass `BaseRepository[Model]`, set `model = Model`   |
| `service.py`      | Business logic — subclass `BaseService[Model]`, raise exceptions from `app/common/exceptions.py` |
| `exceptions.py`   | The module's own error codes — subclass the classes in `app/common/exceptions.py` |
| `dependencies.py` | `Annotated` dependency aliases that wire repository → service         |
| `router.py`       | HTTP layer only — thin, delegates to the service                      |

Request flow: `router → service → repository → database`. Routers never touch the DB
directly; services never build HTTP responses.

To add a module:
1. Create the package with the files above (only those it needs).
2. Import its models in `app/db/models.py` (for migrations).
3. Include its router in `app/api/router.py`.
4. Add tests under `tests/modules/<name>/`.

## Getting started

Development runs **only in Docker** — no local Python or virtualenv is needed.
Prerequisite: [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
cp .env.example .env
docker compose up --build
```

This starts the API (with hot reload) against the database in `.env` (shared Neon DB), plus
your own Redis container. The API refuses to start if it cannot reach either.

- API docs: http://localhost:8000/docs (disabled in production)
- Liveness: `GET /api/v1/health` · Readiness (checks DB and Redis): `GET /api/v1/health/ready`

### Editor (VS Code)

Install the **Dev Containers** extension, then run *Dev Containers: Reopen in Container*.
VS Code runs inside the `api` container, so the Python interpreter, Pylance, Ruff
(format on save) and the test explorer all use the container's environment.

## Common commands

Run from the host while the stack is up (inside the dev container, drop the
`docker compose exec api` prefix):

```bash
docker compose exec api pytest                                        # run tests
docker compose exec api ruff check . --fix                            # lint
docker compose exec api ruff format .                                 # format
docker compose exec api alembic revision --autogenerate -m "message"  # create a migration
docker compose exec api alembic upgrade head                          # apply migrations
docker compose down                                                   # stop (add -v to wipe the DB)
```

After changing `requirements*.txt`, rebuild: `docker compose up --build`.

## Database

The team shares one [Neon](https://neon.tech) Postgres database. Put its two URLs (pooled and
direct, from the password manager) in `.env`; `docker compose up` and a local run both use them.
On startup Compose runs `alembic upgrade head` only when `DB_SCHEMA` is a `dev_<name>` schema;
nobody migrates `public` by hand. For an offline Postgres, use `docker compose --profile local-db up`
(see the comment in `docker-compose.yml`).

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | Neon **pooled** URL (`-pooler` host), used by the app. Paste it as Neon shows it. |
| `MIGRATION_DATABASE_URL` | Neon **direct** URL (no `-pooler`), used by Alembic. Empty = reuse `DATABASE_URL`. |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_SSL` | Local Postgres only; ignored while `DATABASE_URL` is set. |
| `DB_SCHEMA` | `public` is the main schema and only CI migrates it. Use `dev_<yourname>` for your own work. |
| `DB_USE_POOLER` | `true` for Neon's pooled URL (PgBouncer, transaction mode). |

Check your settings without starting the API (the API also checks on startup and refuses
to start if it can't connect):

```bash
python -m app.db.check
```

Try a migration in your own schema (Alembic creates it if needed):

```bash
DB_SCHEMA=dev_yourname alembic upgrade head
```

Rules for the shared DB:

- Only CI migrates `public`, after a merge to `development`. Never run `alembic upgrade` or
  `alembic downgrade` against it by hand. To undo a change, write a new forward migration.
- Work in your own `dev_<name>` schema and never touch anyone else's.
- Add columns as nullable or with a default first, so teammates' running code keeps working.
- A record's state is a `status` column (text + CHECK, e.g. `active` / `disabled`), never an
  `is_active` flag. Settings people choose (`is_public`, `visible_on_discover`) stay booleans,
  and "when" columns (`revoked_at`, `disabled_at`) sit next to the status.
- Backfills use real rows only. When old rows can't know a value (who removed a Notch before it
  was recorded), leave it empty or say "Not recorded (before migration 0012)"; never make it up.
- Never use the `neondb_owner` login in `.env`. The app connects as `notchs_app` and Alembic as
  `notchs_migrator`.

## IDs and public codes

- Every table's primary key is `id`, a UUIDv7 made by the app (`app/common/ids.py`), so new rows
  land at the end of the index. Foreign keys (`<entity>_id`) always hold this UUID.
- Tables people read also get a public code, `<entity>_code`, e.g. `VEN-7K3M9QX2`: a 3-letter
  prefix and 8 random Crockford Base32 characters (no I, L, O, U). Use it in support chats, admin
  screens and URLs, never as a foreign key. Codes are stored uppercase; lookups accept any case.
- Declare one with `venue_code: Mapped[str] = public_code_column("VEN")` and validate it in
  schemas with `PublicCode`.

| Table | Column | Prefix |
|---|---|---|
| `venues` | `venue_code` | `VEN` |
| `admin_users` | `admin_user_code` | `ADM` |
| `venue_applications` | `application_code` | `APP` |
| `visit_claims` | `claim_code` | `CLM` |
| `admin_roles` | `admin_role_code` | `ROL` |

Add a row here when a new table gets a code. A prefix is never reused or changed.

**Venue scan codes** are different: `venues.scan_code` (e.g. `KAHWA`) is what customers send to
get notched. It is made from the venue's name when the venue is created (`KAHWA`, then `KAHWA2`...),
3–10 capital letters and digits starting with a letter, unique, and an admin may change it. The
counter phone's QR opens WhatsApp with **"Hey Notchs!, get me notched. KAHWA-482913"**, where
`482913` is the live code it shows. `GET /api/v1/display/config` returns `scan.link_template`: replace
`{code}` with the live code and draw it as a QR.

## Redis

Redis holds rate limits, one-time codes, leaderboards and the job queue. Unlike Postgres it is
**not shared**: `docker compose up` starts a `redis` container for you and the API reaches it at
`redis://redis:6379/0`. Existing `.env` files need no change. Set `REDIS_URL` in `.env` only to
point at a different Redis.

```bash
docker compose exec redis redis-cli            # inspect keys
docker compose exec redis redis-cli FLUSHDB    # wipe your local Redis
```

## Auth, email, CAPTCHA and WhatsApp settings

Local work needs none of these: the defaults use a built-in development JWT secret, write
emails to the log instead of sending them, and `.env.example` skips the CAPTCHA. Staging and
production refuse to start until the real values are set.

| Setting | Purpose |
|---|---|
| `JWT_SECRET` | Signs the session cookies. At least 32 characters outside local/development. |
| `ACCESS_TOKEN_TTL_MINUTES`, `REFRESH_TOKEN_TTL_DAYS` | Session lifetimes (15 minutes, 30 days). |
| `TOTP_ENCRYPTION_KEY` | Fernet key that encrypts counter phones' code secrets. Required outside local/development. **Never change it without re-encrypting**: existing counters would stop working. |
| `EMAIL_DRY_RUN` | `true`: log emails (with their codes and links) instead of sending. Must be `false` in production. |
| `RESEND_API_KEY`, `EMAIL_FROM` | Resend account key and sender. Needed when `EMAIL_DRY_RUN=false`, unless `SMTP_HOST` is set. |
| `SMTP_HOST`, `SMTP_PORT` | Local and development only: send to this server instead of Resend. Set `SMTP_HOST=mailpit` with `EMAIL_DRY_RUN=false` to read every email at http://localhost:8025. Mailpit delivers nothing. |
| `VENUE_APP_URL`, `ADMIN_APP_URL`, `CUSTOMER_APP_URL` | Where links point, e.g. `https://venue.notchs.com`, `https://admin.notchs.com`, `https://www.notchs.com`. https outside local/development. |
| `ADMIN_NOTIFY_EMAIL` | Optional. Gets a "New venue application" email (no applicant contact details in it). |
| `TURNSTILE_SECRET` | Cloudflare Turnstile secret for the CAPTCHA on public forms. |
| `TURNSTILE_DISABLED` | `true` skips the CAPTCHA. Local and development only. |
| `WHATSAPP_DRY_RUN` | `true`: WhatsApp replies are written to the log instead of sent (phone numbers masked). Must be `false` in production. |
| `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` | From Meta (WhatsApp → API Setup). Needed when `WHATSAPP_DRY_RUN=false`. |
| `WHATSAPP_API_VERSION` | Meta Graph API version, e.g. `v23.0`. |
| `WHATSAPP_APP_SECRET` | Meta app secret (App settings → Basic). Checks the signature on every webhook call; without it, all are refused. Required in production. |
| `WHATSAPP_VERIFY_TOKEN` | Any random string; also entered in Meta's webhook form. Required in production. |
| `WHATSAPP_PUBLIC_NUMBER` | The number customers message, digits with the country code (e.g. `15556755530`, the test number). Counter phones put it in their scan QR. Required in production. |
| `WHATSAPP_STAMP_IMAGE_URL` | Optional public https image sent with a stamp. Unset: the stamp is a text message. |
| `WHATSAPP_SWEEP_INTERVAL_SECONDS` | How often the API retries incoming messages left unhandled (default `60`; `0` turns it off). |

### Sessions (for the frontends)

Logins are kept in HttpOnly cookies, never in the response body, one pair per app:
`nx_venue_*` (venue app), `nx_admin_*` (admin panel), `nx_user_*` (customer site).

- The access cookie (`nx_<app>_at`) lasts 15 minutes and is sent with every request.
- When an API call returns `401 auth.not_authenticated`, call `POST /api/v1/auth/<app>/refresh`
  once and retry. If refresh also returns 401, send the person to the login page.
- `POST /api/v1/auth/<app>/logout` ends the session and clears the cookies.

`<app>` is `venue`, `admin` or `user`. Each Next.js app should proxy `/api/*` to this backend,
so the cookies belong to the app's own domain.

Venue app endpoints (all under `/api/v1`):

| Endpoint | Body | Notes |
|---|---|---|
| `POST /venue/auth/login` | `{email, password}` | Sets the cookies. Same error for any wrong combination; 5 failures lock it for 15 minutes. |
| `POST /venue/auth/set-password` | `{token, password}` | The page behind the emailed link (`/set-password?token=...`), for the first password and for resets. Logs in. |
| `POST /venue/auth/forgot-password` | `{email, turnstile_token}` | Always `202` with the same message. |
| `GET /venue/me` | – | The logged-in member and their venue. Call it when the dashboard loads. |
| `POST /venue/auth/change-password` | `{current_password, new_password}` | Logged in. Sets new cookies: this device stays logged in, other devices are logged out. |
| `POST /venue/me/email/send-code` | `{new_email, current_password}` | Logged in. Emails a 6-digit code to the new address. Always `202`. |
| `POST /venue/me/email/verify` | `{code}` | Logged in. Switches the login email; the old address gets a notice. |

Locally, emails are written to the API log (`EMAIL_DRY_RUN=true`), so copy links from there, or
read them in Mailpit at http://localhost:8025 (`EMAIL_DRY_RUN=false`, `SMTP_HOST=mailpit`).

"Apply as a venue" form (public, no login):

| Endpoint | Body | Notes |
|---|---|---|
| `POST /venue-applications/email/send-code` | `{email, turnstile_token}` | Emails a 6-digit code (10 minutes). One resend per minute. |
| `POST /venue-applications/email/verify` | `{email, code}` | Returns `verification_token` (30 minutes). |
| `POST /venue-applications` | the form + `verification_token` | `201 {application_code}`. Fields: `venue_name`, `category`, `city_id`, `google_maps_url`, `contact_name`, `phone`; optional `landmark`, `latitude` + `longitude`, `notes`. |

Counter phone (one per venue). The owner pairs a spare phone that then shows the rotating code:

| Endpoint | Who | Notes |
|---|---|---|
| `GET /venue/counter` | owner | Label, paired, `online` (checked in within 2 minutes), last seen. 404 before the first pairing link. |
| `POST /venue/counter/pairing-link` | owner | `{url, expires_at}`: open on the phone (or show as a QR it scans). 10 minutes, single use. Creates the counter the first time, re-pairs after. |
| `PATCH /venue/counter` | owner | `{label}` |
| `DELETE /venue/counter` | owner | Removes the counter; the phone stops on its next call. |
| `POST /display/pair` | phone | `{token}` from the link. Sets the `nx_device` cookie (a year, `/api/v1/display` only) and returns the display config. |
| `GET /display/config` | phone | Venue, label and TOTP settings (`secret` base32, 6 digits, 30 s, SHA1): the phone makes the codes itself, offline. |
| `POST /display/heartbeat` | phone | "I'm on", for the owner's online dot. Call about once a minute. |

Pairing gives the counter a new secret, so a replaced phone's codes stop working. The first pairing
moves the venue from `signed` to `counter_live`.

Customer site (`nx_user_*` cookies): customers log in with the link the WhatsApp bot sends after
they type LOGIN, or send what the website's "Continue with WhatsApp" button pre-types,
`Hey Notchs, I want to login` (open `https://wa.me/<WhatsApp number>?text=Hey%20Notchs%2C%20I%20want%20to%20login`).
New numbers get an account and the consent question first, the same as a first scan.
The link opens `/login/verify?token=...`; that page must `POST /auth/customer/consume`
with `{token}` (opening the link alone does nothing, so WhatsApp's link preview can't use it up).
The link works once and expires after 15 minutes.

Admin panel (`nx_admin_*` cookies): `POST /admin/auth/login`, `/admin/auth/set-password`,
`/admin/auth/forgot-password`, `/admin/auth/change-password`, `GET /admin/me`, and the review queue:

| Endpoint | Who | Notes |
|---|---|---|
| `GET /admin/venue-applications?status=pending&page=1&size=20` | admin, reviewer | Oldest first. `status`: `pending` (default), `approved`, `rejected`. |
| `GET /admin/venue-applications/{id or APP-code}` | admin, reviewer | One application, with the Maps link. |
| `POST /admin/venue-applications/{ref}/approve` | admin | Body (all optional): `area_id`, `address`, `latitude` + `longitude`. Creates the venue (`signed`) and the owner's login, then emails the set-password link. |
| `POST /admin/venue-applications/{ref}/reject` | admin | Body: `reason`, `notify` (default `true` emails the applicant). |

Admin Settings (`/admin/users`, role `admin` only): `GET` lists every admin; `POST {name, email, role}`
adds one and emails a set-password link; `PATCH /{ADM-code} {role?, is_active?}` changes the role
or turns them off (which logs them out at once); `POST /{ADM-code}/link` emails a new link. Nobody
can remove their own admin access and there is always an active admin. The CLI below is only
needed for the very first admin.

Each admin has a `status`: `invited` until they use their set-password link, then `active`, or
`disabled` when turned off (with who did it and when). Roles are rows in `admin_roles`: until the
roles screen exists there are two built-in ones, "Admin" (every permission) and "Reviewer", and
the API's `role` (`admin` / `reviewer`) maps onto them. Venue logins have the same three statuses.

## Command-line tasks

```bash
docker compose exec api python -m app.cli seed            # Ahmedabad + areas; safe to repeat
docker compose exec api python -m app.cli create-admin --email you@notchs.com --name "Your Name"
docker compose exec api python -m app.cli admin-link --email you@notchs.com   # new set-password link
```

They use the database and schema in `.env`. `create-admin` prints the set-password link (and emails
it unless `EMAIL_DRY_RUN=true`); `--role reviewer` makes a view-only admin.

**The first admin without a shell** (e.g. Render's free plan): set `FIRST_ADMIN_EMAIL` (and
`FIRST_ADMIN_NAME`) on the API and redeploy. At startup, only while no admin exists at all, that
address becomes an admin and is emailed a set-password link (`app/modules/admin_users/bootstrap.py`).
Once any admin exists it does nothing; add more admins in admin Settings. If the email doesn't
arrive, use "Forgot password" on the admin login page.

## WhatsApp webhook

Meta delivers incoming WhatsApp messages and delivery statuses to
`/api/v1/webhooks/whatsapp`. In Meta (WhatsApp → Configuration → Webhook) set:

- **Callback URL:** `https://<api host>/api/v1/webhooks/whatsapp`
- **Verify token:** the value of `WHATSAPP_VERIFY_TOKEN`
- **Webhook fields:** subscribe to `messages`

Every call must carry Meta's `X-Hub-Signature-256` signature (made with `WHATSAPP_APP_SECRET`).
Messages are stored in `whatsapp_messages` once each, even if Meta delivers them again;
statuses (`delivered`, `read`, `failed`) update the messages we sent and never move backwards.

After answering Meta, the API handles each new message once (the row is locked while it runs).
A message whose handling fails is retried, at most 3 times, then marked
`error_code = processing_failed`. Every `WHATSAPP_SWEEP_INTERVAL_SECONDS` (and at startup) the
API also picks up messages left unhandled, e.g. by a restart.

What the bot replies:

| Customer sends | Bot |
|---|---|
| Their first message | Creates the customer (the phone number is their identity) |
| `LOGIN`, `Hey Notchs, I want to login` (pre-typed by the website), or taps **🔑 Login link** | A single-use passport login link (15 minutes; at most 3 links per 15 minutes). If they have not agreed yet, the consent question instead |
| A scan: `Hey Notchs!, get me notched. KAHWA-482913` | Counts the visit: stamp, notch number, points and a login link. Or says why not (wrong or expired code, already notched today, already a Notch in this niche today); the same refusal at most once a minute |
| `CHANGE 123456` from a new number (code from `POST /me/phone-change`, 10 minutes, works once) | Moves the customer's account to that number. Refused if the number already has its own account; never creates one. Wrong codes: 5 per number per 15 minutes |
| The scan sentence with no code | No stamp, a login link |
| `CLAIM CLM-7K3M9QX2` (pre-typed by the passport's claim link) | "Now send the bill photo". Or says the photo already came, or that no open claim has that code (at most once a minute) |
| A photo | Goes to their claim waiting for a photo today (or the one named in the caption): "we'll check it soon". With no such claim: how to start one (at most once a minute) |
| Taps **I agree, I'm 18+** | Records the privacy consent and 18+ confirmation, replies with a login link |
| Picks from the **What did you try?** menu (sent after a stamp, once they have agreed, if the venue has items) | Saves the answer for that visit, up to 24 hours later. Several answers are fine. No points |
| Anything else | A short help message with a **🔑 Login link** button (at most once every 10 minutes) |

Customers who have not agreed yet never get a link, only the consent question; a scan's visit
still counts. Blocked and deleted customers get no reply. With `WHATSAPP_DRY_RUN=true` replies, login links
included, are written to the log instead of sent.

### Trying the bot without WhatsApp

Logged in as an admin (role `admin`), outside production:

```
POST /api/v1/dev/simulate-inbound
{"phone": "98765 43210", "text": "LOGIN"}
{"phone": "98765 43210", "button": "login"}   # a tap on the 🔑 Login link button
{"phone": "98765 43210", "text": "Hey Notchs!, get me notched. KAHWA-482913"}   # a scan: needs the counter's real live code
{"phone": "98765 43210", "button": "consent_agree"}   # a tap on I agree, I'm 18+ (a new number needs this before a link)
```

The text goes through the same steps as a real WhatsApp message, and the response lists the
bot's replies (`data.replies`, buttons shown as `[title]` lines), login links included.
Nothing is sent to Meta.

## The owner's own place

One login per place (team decision): the owner's. With it they see and edit their place, but
never the numbers, which only come from real Notchs (`app/modules/venues/profile.py`).

| Endpoint | Notes |
|---|---|
| `GET /venue/profile` | Name, kind of place, scan code, status, city, area, address, landmark, description, opening hours |
| `PATCH /venue/profile` | Any of `name`, `description` (the tagline, 160), `about` (1000), `address` (300), `landmark` (120), `area_id` (in its city), `opening_hours`, `menu_url` and `website_url` (https links), `instagram_handle`, `contact_phone` (the place's own number), `amenities` (from the fixed list in `venues/amenities.py`), `visible_on_discover`. Kind of place, scan code, status and billing can't be changed here; renaming keeps the link and the scan code |
| `GET /venue/billing` | `amount_due_paise` (0), `free_months` (3), and the founding discount: `founding_no`, `founding_spots`, `founding_spots_left`, `founding_discount_percent` |

**Founding places and free months** (team decisions, `app/modules/venues/founding.py`): every place
pays nothing for its first 3 months, then the subscription charges apply (amount not decided).
The first 100 places of each kind (the first 200 turfs, box grounds and gyms) get 25% off for life.
A place gets its founding number, within its kind, when the team approves it. Kinds of place:
café, restaurant, bar, hotel, turf / box ground / gym, theatre, other.

## Deleting an account

A customer deletes their account from the passport (`POST /me/delete` with `{"confirm": "DELETE"}`),
or an admin does it when they ask support (`POST /admin/customers/{handle}/delete` with the handle
typed again; `admin` role). It is permanent (team decision) and happens at once
(`app/modules/users/deletion.py`):

- **Deleted:** phone number, name, Instagram, area, consent (and its stored wording), logins, bill-photo claims, invites and
  their Star Points, and the number, text and raw data of every WhatsApp message to or from them.
- **Kept, anonymous (team decision):** their visits and points, so venues' past totals don't change.
  The account row stays as an empty shell (status `deleted`, random handle, no number). It is never
  shown on leaderboards, admin lists or venue screens and can't log in.
- A later message from the same number starts a brand-new account.

## Bill-photo claims

When a counter phone is down, a customer can still get the Notch: they start a claim in their
passport, send `CLAIM CLM-...` and then a photo of the bill on WhatsApp, the same day (India time),
and an admin checks it. Notchs stores only WhatsApp's id for the photo, never the photo; the admin
screen fetches it from WhatsApp when opened. **WhatsApp keeps media about 30 days**, so claims must be
checked within that time. An approved claim is a normal visit with points, marked `verified`.

| Endpoint | Who | Notes |
|---|---|---|
| `GET /me/claims/venues?q=` | Logged-in customer | Live places by name (2–60 characters), up to 10 |
| `POST /me/claims` | Logged-in customer | `{venue_code}`. A `CLM-` code and a WhatsApp link pre-typed with `CLAIM CLM-...`. Up to 3 a day; one per place a day; not where they already have a Notch today, or one of that kind |
| `GET /me/claims` | Logged-in customer | Their last 20 claims: `awaiting_photo`, `expired` (no photo that day), `in_review`, `approved` (with the Notch number) or `rejected` (with the reason) |
| `GET /admin/claims?status=in_review` | admin, reviewer | Oldest first. The customer's handle, never their number, and when the counter phone last checked in |
| `GET /admin/claims/{code}` | admin, reviewer | One claim |
| `GET /admin/claims/{code}/photo` | admin, reviewer | The image, fetched from WhatsApp now (`Cache-Control: no-store`). 410 once WhatsApp no longer has it, or in dry-run mode |
| `POST /admin/claims/{code}/approve` | admin, reviewer | Checks the day's limits again, then adds the visit |
| `POST /admin/claims/{code}/reject` | admin, reviewer | `{reason}` (3–200 characters), shown to the customer, and `code` (optional, default `other`): `unreadable`, `wrong_venue`, `wrong_date`, `duplicate_bill` or `other` |

The customer hears the result on WhatsApp only while that is a free reply (within 24 hours of their
own last message); otherwise it shows in their passport. No paid templates.

## Leaderboards

Ranks live in Redis per city, this month (India time) and all time, and points go on the board of
the city the visit happened in. Postgres (`points_ledger`) is the truth: boards are rebuilt from it
at startup and every `LEADERBOARD_REBUILD_INTERVAL_SECONDS`, so an emptied Redis repairs itself. To
do it now: `python -m app.cli rebuild-leaderboards`.

**Rank decay** (team decision, provisional; the rate is ours, in `app/modules/leaderboards/decay.py`):
after 15 days without a Notch, the points that count for rank drop 10% a week (day 16: 90%, day 23:
80%... down to 0%), and the next Notch brings them all back. The points a customer earned never
change: the passport shows them all and `points_ledger` is untouched, so changing the rule only takes
a rebuild. `GET /leaderboards/me` returns `rank_points_percent` and `rank_drops_on`.

Everyone with points is ranked and equal points share a rank. Customers are private by default: they
keep their place, but their row has no name or handle until they choose to be public.

| Endpoint | Who | Notes |
|---|---|---|
| `GET /leaderboards?city_id=&period=month&limit=50` | Public | `period` is `month` or `all`; `limit` up to 100; 60 calls a minute per IP |
| `GET /leaderboards/me` | Logged-in customer | Their rank and points in their home city, and `is_public` |
| `PATCH /leaderboards/me` | Logged-in customer | `{is_public}`: show or hide their name |

## Tags

One shared list, and only admins change it. Kinds: `cuisine`, `vibe`, `activity`, `item`. There is
no starter list, so venues have nothing to pick until an admin adds tags.

| Endpoint | Who | Notes |
|---|---|---|
| `GET /admin/tags`, `GET /admin/tags/{id}` | Admin or reviewer | `?kind=item` filters; paginated |
| `POST /admin/tags` | Admin | `{name, kind}`. The slug comes from the name; a taken one is `409 tags.name_taken` |
| `PATCH /admin/tags/{id}` | Admin | `{name?, kind?}`. The kind cannot change once in use (`409 tags.kind_locked`) |
| `DELETE /admin/tags/{id}` | Admin | Also takes it off venues' lists. Refused once a customer has picked it (`409 tags.in_use`) |
| `GET`, `PUT /venue/tags` | Venue owner | `{tag_ids}`: what the place is. At most 5, any kind but `item` |
| `GET`, `PUT /venue/items` | Venue owner | `{tag_ids}` in menu order: the bot's "What did you try?" menu. At most 10, kind `item` only |

Pickers get their options from `GET /api/v1/dropdowns/tags?kind=item`.

## Dropdowns

Every form gets its option lists from one endpoint, as `[{value, label}]`:

```
GET /api/v1/dropdowns/venue_categories
GET /api/v1/dropdowns/cities                 # active cities only
GET /api/v1/dropdowns/areas?city_id=<uuid>
GET /api/v1/dropdowns?names=cities,venue_categories   # several at once (max 10)
```

To add a list, call `register(Dropdown("name", loader))` in your module's `dropdowns.py` and
import that file in `app/modules/dropdowns/lists.py`. No new endpoint is needed.

## Docker images

The `Dockerfile` is multi-stage:

- `dev` — runtime + test/lint tooling; source is bind-mounted by `docker-compose.yml`.
- `prod` — runtime dependencies and application code only, runs as a non-root user:
  `docker build --target prod -t notchs-api .`

## Contributing

Branch protection is not enforced on this private repo (GitHub Free), so these rules are
a team agreement. CI still runs on every PR.

### Branches

- `main`: production-ready code. Only updated by a PR **from `development`** for a release.
- `development`: integration branch. Every work branch starts here and comes back here.
  Merging into it migrates the shared Neon DB's `public` schema (CI job `migrate-shared-db`).
- `feature/<module-name>-module`: new features, e.g. `feature/auth-module`.
- `fix/<fix-name>`: bug fixes, e.g. `fix/login-token-expiry`.

### Rules

1. **Never push directly to `development` or `main`.** Always open a PR.
2. **Merge only when CI is green** ("Lint, tests, migrations") and one teammate has approved.
3. **PRs into `development`**, never from a feature branch straight into `main`.
4. **Update your branch before merging** if `development` moved on (`git pull origin development`),
   so CI tests what will actually land.
5. **Migrations:** try them in your own `dev_<name>` schema first, one migration per PR where
   possible. If CI reports multiple Alembic heads, rebase and re-point `down_revision`.
   Never run `alembic upgrade` against `public` by hand.
6. **Never commit secrets** (`.env`, database URLs, dumps). Share credentials through the
   password manager only.
7. **Commit messages** start with the branch name: `feature/<module-name>-module: <message>`,
   e.g. `feature/scans-module: add tags and the menu` (bug fixes: `fix/<fix-name>: <message>`).

### Workflow

```bash
git checkout development && git pull           # start from the latest development
git checkout -b feature/auth-module            # create your branch
# ... work, commit ...
git push -u origin feature/auth-module         # then open a PR into development on GitHub
```

After the PR is merged, delete the branch and start the next one from `development` again.
