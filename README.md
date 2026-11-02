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
│       ├── audit/              # Audit log (G6) and the admin overview counts
│       ├── auth/               # Sessions, venue + admin login, passwords, email codes
│       ├── cards/              # Monthly Notch Cards: customers' (C9, C10, A12) and venues' (E7)
│       ├── dropdowns/          # GET /dropdowns/{name}: option lists for every form
│       ├── health/
│       ├── jobs/               # Scheduler, monthly jobs (G5), "counter phone is off" emails
│       ├── locations/          # Cities and areas; days, weeks and months in a city's timezone
│       ├── notches/            # Check-ins: the counter code, Notchs, badges, fraud signals, G1/G4
│       ├── passport/           # The customer's passport, /me (C1-C14)
│       ├── rankings/           # Leaderboards, venue rankings, trending
│       ├── site/               # The public website's data, /public (A1-A15)
│       ├── users/              # Customers: founding numbers, deletion and wipe, admin (G3)
│       ├── venue_applications/ # "Apply as a venue" form + admin review (approve/reject)
│       ├── venues/             # Venues, members, counter phone, owner dashboard (E5-E14), G2
│       └── whatsapp/           # WhatsApp: messages, sender, webhook, processing, the bot (B1-B10)
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
| `users` | `user_code` | `USR` |
| `notches` | `notch_code` | `NCH` |

Notch Cards use their own printed format instead, `NC-1026-0142` (month, year, number that
month), because the code is part of the card's design.

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
| `RESEND_API_KEY`, `EMAIL_FROM` | Resend account key and sender. Needed when `EMAIL_DRY_RUN=false`. |
| `VENUE_APP_URL`, `ADMIN_APP_URL`, `CUSTOMER_APP_URL` | Where links point, e.g. `https://venues.notchs.com`, `https://admin.notchs.com`, `https://notchs.com`. https outside local/development. |
| `ADMIN_NOTIFY_EMAIL` | Optional. Gets a "New venue application" email (no applicant contact details in it). |
| `TURNSTILE_SECRET` | Cloudflare Turnstile secret for the CAPTCHA on public forms. |
| `TURNSTILE_DISABLED` | `true` skips the CAPTCHA. Local and development only. |
| `WHATSAPP_DRY_RUN` | `true`: WhatsApp replies are written to the log instead of sent (phone numbers masked). Must be `false` in production. |
| `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` | From Meta (WhatsApp → API Setup). Needed when `WHATSAPP_DRY_RUN=false`. |
| `WHATSAPP_API_VERSION` | Meta Graph API version, e.g. `v23.0`. |
| `WHATSAPP_APP_SECRET` | Meta app secret (App settings → Basic). Checks the signature on every webhook call; without it, all are refused. Required in production. |
| `WHATSAPP_VERIFY_TOKEN` | Any random string; also entered in Meta's webhook form. Required in production. |
| `WHATSAPP_PUBLIC_NUMBER` | The number customers message, digits with the country code (e.g. `15556755530`, the test number). Counter phones put it in their scan QR. Required in production. |
| `WHATSAPP_SWEEP_INTERVAL_SECONDS` | How often the API retries incoming messages left unhandled (default `60`; `0` turns it off). |
| `WHATSAPP_PUBLIC_NUMBER` | The number customers message (E.164). The counter QR opens `wa.me/<number>`. The default is the design's **test** number: set the real one before launch. |
| `SCHEDULER_ENABLED`, `SCHEDULER_INTERVAL_SECONDS` | Background jobs inside the API (see "Background jobs"). Default on, every 60 s. |
| `DEFAULT_VENUE_FEE_PAISE` | The plan after the free months, in paise (GST extra), when a venue has no fee of its own. Empty = billing shows no amount. |
| `MEDIA_BASE_URL` | Public base URL for stored files (venue photos), e.g. a CDN. Empty = no photo URLs yet. |

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

Locally, emails are written to the API log (`EMAIL_DRY_RUN=true`), so copy links from there.

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
| `GET /display/config` | phone | Venue (name, area), label, TOTP settings (`secret` base32, 6 digits, 30 s, SHA1), the QR (`qr.whatsapp_number`, `qr.message_template`), `notched_today` and `server_time`. The phone makes the codes itself, offline. |
| `POST /display/heartbeat` | phone | "I'm on", for the owner's online dot. Call about once a minute; body (optional) `{battery_pct, is_charging}`. Returns `{notched_today, server_time}`: set the code clock from `server_time`. |

Pairing gives the counter a new secret, so a replaced phone's codes stop working. The first pairing
moves the venue from `signed` to `counter_live`.

The QR opens `https://wa.me/<qr.whatsapp_number>?text=<message>`, where the message is
`scan.link_template` with `{code}` replaced by the current code:

```
Hey Notchs!, get me notched. KAHWA-482913
```

(The design's QR spec drew the code as letters, `K7Q-29F [v07]`. The counter was built on
standard TOTP, which any phone can make offline, so the code is 6 digits and the venue ref is
its public code. The bot reads the message leniently: spacing, case and a split `482 913` are fine.)

## Check-ins: how a scan becomes a Notch

The bot hands every message with a venue code and 6 digits to `notches/checkin.py`. The rules
(QR spec, "Verification rules"):

| When | Then | Reply |
|---|---|---|
| The code matches the current or previous 30 s window (or the next: phone clock a little ahead) | Notched: the venue's next `Notch #`, `user_visit_no`, badges | B2 (+ B8, B6) |
| The code is older than that, wrong, or for no live counter | Nothing | B4 |
| The customer already notched this venue today (the city's day, IST) | Nothing | B3 |
| The customer's first ever scan, no consent yet | The checked scan waits 15 minutes in `pending_checkins`; "I agree" turns it into their first Notch (in the same transaction, so two taps or a retry make one Notch) | B1 |
| The counter phone has not checked in for 2 minutes | ± 2 windows accepted; the Notch is `flagged` (`counter_offline`) | B2 |
| The same code from 2+ numbers | All of them notched and `flagged` (`shared_code`) | B2 |
| More than 6 valid scans in a minute at one counter | A fraud signal (G4) | B2 |
| The venue is paused, closed or not live yet | Nothing | "isn't taking Notchs right now" |

Consent (B1) is stored in `user_consents` with the exact wording shown, its version and the time
(`users/consent.py` holds the wording; changing it means a new version).

Times are WhatsApp's own message timestamps, so a message handled late (a retry after a restart)
is judged by when it was sent. `flagged` Notchs count everywhere until a reviewer rejects them;
`rejected` ones count nowhere. A customer can have one counted Notch per venue per day.

Badges (`notches/badges.py`): **First Notch** (one of the venue's first `first_notch_spots`, 50,
customers), **Founding Member** (one of the first 500), **Cafe Hopper** (5 different cafés),
**Night Owl** (5 visits at or after 9 PM, until 4 AM), **Regular** (5 visits to one place in a
calendar month). Founding Member numbers are given at the first website sign-up or first Notch,
whichever comes first; the bot announces the number with the first Notch.

Customer site (`nx_user_*` cookies): customers log in with the link the WhatsApp bot sends after
they type LOGIN. The link opens `/login/verify?token=...`; that page must `POST /auth/customer/consume`
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

## Command-line tasks

```bash
docker compose exec api python -m app.cli seed            # Ahmedabad + areas; safe to repeat
docker compose exec api python -m app.cli create-admin --email you@notchs.com --name "Your Name"
docker compose exec api python -m app.cli admin-link --email you@notchs.com   # new set-password link
```

They use the database and schema in `.env`. `create-admin` prints the set-password link (and emails
it unless `EMAIL_DRY_RUN=true`); `--role reviewer` makes an admin who can review Notchs (G1) and
view everything else, but not change it.

```bash
docker compose exec api python -m app.cli run-job notch_cards --period 2026-10   # (re-)run a month
```

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
| A counter scan (`Hey Notchs!, get me notched. KAHWA-482913`) | See "Check-ins": Notched (B2), already today (B3), expired (B4), or consent first (B1); then Founding Member # (B8, with the first Notch) and new badges (B6) |
| Taps **I agree** (or types `I AGREE`) | Records 18+ and privacy consent, then notches the scan that was waiting |
| Taps **Privacy notice** | The privacy page link |
| `LOGIN`, or taps **🔑 Login link**, **My passport**, **See badges**, **See my card** | A single-use passport login link (15 minutes; at most 3 per 15 minutes), with `&next=/badges` or `&next=/cards/latest` for those buttons |
| `HELP`, or taps **Help** | What the bot understands (B9), with a **🔑 Login link** button |
| `MY DATA` | A login link to `/me/privacy`, where "Download my data" is |
| `DELETE`, then `YES` within 10 minutes | Closes the account at once; the scheduler wipes the data within a minute (B9). The `user_deletions` row is the pending state, so a doubled or retried YES closes it once. |
| Anything else | "I only understand scans. Type HELP for options." (B10, at most once every 10 minutes) |

Blocked and deleted customers get no reply. With `WHATSAPP_DRY_RUN=true` replies, login links
included, are written to the log instead of sent. All copy lives in `whatsapp/replies.py`.

### Trying the bot without WhatsApp

Logged in as an admin (role `admin`), outside production:

```
POST /api/v1/dev/simulate-inbound
{"phone": "98765 43210", "text": "LOGIN"}
{"phone": "98765 43210", "button": "login"}   # a tap on the 🔑 Login link button
{"phone": "98765 43210", "text": "Hey Notchs!, get me notched. KAHWA-482913"}
{"phone": "98765 43210", "button": "consent_yes"}   # "I agree" after the first scan
```

For a scan, use the current code of a paired counter (its `GET /display/config` secret in any
TOTP app, or `python -c "from app.modules.notches.codes import totp, window_of; import time;
print(totp('<secret>', window_of(time.time(), 30)))"`).

The text goes through the same steps as a real WhatsApp message, and the response lists the
bot's replies (`data.replies`, buttons shown as `[title]` lines), login links included.
Nothing is sent to Meta.

## Customer passport (`/me`, `nx_user_*` cookies)

| Endpoint | Screen | Notes |
|---|---|---|
| `GET /me`, `PATCH /me` | C1, C11, C12 | Profile; `needs_name` means show the claim screen. PATCH `name`, `area_id`, `theme`, `is_public`. |
| `GET /me/home` | C2 | This month's count, rank (from the 3rd Notch, else `notches_to_rank`), ONE next step, ONE unnotched place. |
| `GET /me/notches?month=2026-10` | C3 | Newest first; group by `local_date`. Plus all-time totals. |
| `GET /me/notches/{NCH-code}`, `POST …/report {message}` | C4 | Report a mistake: the Notch goes to the review queue. |
| `GET /me/badges` | C8 | Earned, and locked ones with progress. |
| `GET /me/leaderboard?period=week\|month\|all&category=&area_id=` | C7 | Your row, top 10; private customers show as "Hidden explorer". |
| `GET /me/places?lat=&lng=&area_id=&category=&open_now=` | C5 | By distance with a location, else by area; one tag per place. |
| `GET /me/venues/{VEN-code}` | C6 | The venue page plus your visits. |
| `GET /me/cards`, `GET /me/cards/latest`, `GET /me/cards/{NC-code}` | C9, C10 | Card with every face's words and the reveal screens. |
| `PATCH /me/cards/{code} {face, revealed}` | C9 | "Change look"; mark the reveal seen. |
| `POST /me/cards/{code}/share`, `DELETE …/share` | C9, A12 | Sharing turns on `/card/<code>`; call it before the share sheet. |
| `GET /me/year/2026` | C14 | The year recap. |
| `GET /me/export` | C12 | Download my data (JSON file). |
| `POST /me/delete {"confirm": true}` | C12 | Logs out; the data is wiped within a minute. |

## Website (`/public`, no login)

| Endpoint | Page | Notes |
|---|---|---|
| `POST /public/join` | A1 stop 5, A14 | `{name, phone, area_id?, agree: true, turnstile_token}`. Reserves a Founding Member number. A number already on the list gets `already_joined` and nothing else. Never counts as WhatsApp consent. |
| `GET /public/stats` | A1, A3 | Founding seats left: members (of 500), venues (of 30). |
| `GET /public/cities/{city}` | A6 | Category tiles, trending this week, areas, month stats, `has_rankings`. |
| `GET /public/cities/{city}/rankings/{category}?sort=most_visited\|trending&area=&week=` | A7 | `category`: `cafes`, `restaurants-and-bars`, `turfs`, `restaurants`, `bars`, `hotels`. The last full week (Mon-Sun) by default; `week` is a Monday. |
| `GET /public/cities/{city}/venues/{slug}` | A8 | Hours and open now, directions, First Notch spots, "Regular right now" (public customers only). |
| `GET /public/cards/{NC-code}` | A12 | Shared cards only; 404 `cards.not_shared` otherwise. Logged-in viewers don't see places they already notched. |
| `GET /public/pulse` | A4 | Every finished month: visits verified, fakes blocked; `first_pulse_on` before the first. |

Founding Venues: the first 30 approved applications get a number and 2 months free
(`free_until`), set at approval.

## Venue dashboard (`/venue`, `nx_venue_*` cookies)

Totals only: never a customer's phone number or name.

| Endpoint | Screen | Notes |
|---|---|---|
| `GET /venue/home?range=7d\|30d` | E5 | Headline, bars, busiest 2 hours, counter state. `unlocked` after 10 visits. |
| `GET /venue/daily?from=&to=` | E5 | Day by day, up to 92 days. |
| `GET /venue/customers?range=7d\|30d` | E6 | New, returning, regulars (5+ visits this month), today's visits. |
| `GET /venue/visits?date=`, `GET /venue/visits.csv?from=&to=` | E6 | Visits as "6th visit", and the CSV export. |
| `GET /venue/profile`, `PATCH /venue/profile` | E8 | The page as customers see it; edit description, hours, tags (up to 3), address, landmark. |
| `PATCH /venue/me/contact` | E11 | Owner name and WhatsApp number. |
| `GET /venue/cards`, `GET /venue/cards/2026-10` | E7 | Monthly venue cards (made on the 1st). |
| `GET /venue/certificate` | E14 | Founding Venues only. |
| `GET /venue/billing` | E10 | Plan, free months, visits this cycle. Payments are not connected yet. |
| `POST /venue/close-request` | E11 | "Delete venue": emails the team; a person closes it. |

## Admin panel (`/admin`, `nx_admin_*` cookies)

Reviewers can view everything and decide proof review; everything else needs role `admin`.

| Endpoint | Page |
|---|---|
| `GET /admin/overview` | Counts for the nav |
| `GET /admin/notches?status=flagged`, `GET /admin/notches/{ref}`, `POST …/approve`, `POST …/reject` | G1 proof review |
| `GET /admin/venues?status=&q=`, `GET /admin/venues/{ref}`, `PATCH …`, `POST …/status` | G2 venues |
| `GET /admin/users?q=`, `GET /admin/users/{ref}` (audited), `POST …/block`, `…/unblock`, `…/delete`, `GET /admin/user-deletions` | G3 users |
| `GET /admin/fraud-signals?status=open`, `POST /admin/fraud-signals/{id}/resolve` | G4 fraud flags |
| `GET /admin/jobs`, `POST /admin/jobs/{job}/run {period}` | G5 monthly jobs |
| `GET /admin/audit-log` | G6 audit log |

## Background jobs

The scheduler runs inside the API (`SCHEDULER_ENABLED`), one tick a minute:

| When | Job |
|---|---|
| Every minute | Wipe the data of closed accounts (DELETE + YES, or "Delete everything") |
| Every 5 minutes | Email owners whose counter phone has been silent 10+ minutes while open |
| 1st of the month, 06:00 IST | `notch_cards`: a card for every customer with a Notch last month |
| 06:30 | `venue_cards`: venues' cards and the "Your Notch Card is ready" emails |

No job sends a paid WhatsApp template: "Your card is ready" (B7) is business-initiated, costs
money and needs Harsh's or Abhishek's approval first. Each month is claimed once in `job_runs`, so restarts and several API instances are safe; a
job missed on the 1st runs on the next tick that finds it due. Re-running a month (admin or
`run-job`) only finishes what failed.

## Tests

`pytest` runs everything. Database tests need `TEST_DATABASE_URL`, a Postgres they may create
and drop schemas in (CI sets it to its throwaway Postgres); without it they are skipped, so
they never touch the shared Neon database. Locally:

```bash
docker compose --profile local-db up -d db
docker compose exec -e TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/notchs api pytest
```

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
7. **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/):
   `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.

### Workflow

```bash
git checkout development && git pull           # start from the latest development
git checkout -b feature/auth-module            # create your branch
# ... work, commit ...
git push -u origin feature/auth-module         # then open a PR into development on GitHub
```

After the PR is merged, delete the branch and start the next one from `development` again.
