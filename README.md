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
│   │   └── request_context.py  # X-Request-ID, caller IP (for the audit log), request timing logs
│   └── modules/                # One package per feature
│       ├── admin_users/        # Notchs staff accounts, roles and permissions (login is in auth)
│       ├── audit/              # Audit log: audit.record() for every module, GET /admin/audit-logs
│       ├── auth/               # Sessions, venue + admin login, passwords, email codes
│       ├── dropdowns/          # GET /dropdowns/{name}: option lists for every form
│       ├── health/
│       ├── locations/          # Cities and areas (models only)
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

Add a row here when a new table gets a code. A prefix is never reused or changed.

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
| `GET /display/config` | phone | Venue, label and TOTP settings (`secret` base32, 6 digits, 30 s, SHA1): the phone makes the codes itself, offline. |
| `POST /display/heartbeat` | phone | "I'm on", for the owner's online dot. Call about once a minute. |

Pairing gives the counter a new secret, so a replaced phone's codes stop working. The first pairing
moves the venue from `signed` to `counter_live`.

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
it unless `EMAIL_DRY_RUN=true`). The new admin gets the **Owner** role unless `--role` names another
role (any case), e.g. `--role reviewer`.

## Admin roles and permissions

Every admin has one role, and a role is a list of permissions ticked in the admin panel
(`GET /api/v1/admin/permissions` lists them, grouped, with plain-language labels).

- **Owner** (built in): every permission, including ones added later. It cannot be edited or
  deleted.
- **Reviewer** (built in, editable): can look at applications, venues, customers, WhatsApp, billing
  and the dashboard; not the audit log or the team.
- Any other role is made at `POST /api/v1/admin/roles` and edited or deleted at
  `/api/v1/admin/roles/{id or ROL- code}`. A role still given to admins cannot be deleted.
- **No self-promotion:** an admin can only give a role permissions they have themselves.
- `GET /api/v1/admin/me` (and every admin login response) returns the admin's role and permissions,
  so the panel can hide what they cannot use.

In code, every admin endpoint needs a permission:

```python
CanReview = Annotated[AdminUser, Depends(require_permission(Permission.APPLICATIONS_REVIEW))]
```

Without it the answer is 403 `auth.permission_required`, with the permission in `details`. A module
that adds admin endpoints adds its permissions to `app/modules/admin_users/permissions.py`.

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
| `LOGIN`, or taps **🔑 Login link** | A single-use passport login link (15 minutes; at most 3 links per 15 minutes) |
| Anything else | A short help message with a **🔑 Login link** button (at most once every 10 minutes) |

Blocked and deleted customers get no reply. With `WHATSAPP_DRY_RUN=true` replies, login links
included, are written to the log instead of sent.

### Trying the bot without WhatsApp

Logged in as an admin whose role has `whatsapp.simulate` (the Owner has it), outside production:

```
POST /api/v1/dev/simulate-inbound
{"phone": "98765 43210", "text": "LOGIN"}
{"phone": "98765 43210", "button": "login"}   # a tap on the 🔑 Login link button
```

The text goes through the same steps as a real WhatsApp message, and the response lists the
bot's replies (`data.replies`, buttons shown as `[title]` lines), login links included.
Nothing is sent to Meta.

## Audit log

Every change and every security event in the project is recorded in `audit_logs`, whoever did it:
an admin, a venue login, a customer, a counter phone, the system (CLI, jobs) or someone not logged
in (`public`, e.g. a failed login or the application form). Each entry has the actor, the action
(`auth.login`, `application.approved`, `counter.paired`, ...), the record acted on, the changed
fields before and after, the caller's IP and the request id.

```python
from app.modules.audit import service as audit

await audit.record(
    session,
    audit.Actor.admin(admin),
    "venue.status_changed",
    venue,
    before={"status": "signed"},
    after={"status": "paused"},
)
```

Rules for every module:

- **Record every change and security event** (logins, failed logins, password and email changes,
  sessions ended), plus sensitive reads such as showing a customer's phone number. Plain reads and
  routine session refreshes are not recorded; the request log has them.
- The entry is saved in the **same transaction** as the change. To keep an entry for a request that
  fails (a failed login), commit before raising.
- **Never** put phone numbers, passwords, tokens or secrets in `before`/`after`. `audit.clean()`
  drops keys that look like them anyway.
- The table is **append-only**: the app's database role can insert and read, nothing else. It is
  partitioned by month (partitions up to December 2027, plus a default one).

Admins read it at `GET /api/v1/admin/audit-logs` (newest first; filter by actor, action or prefix
such as `auth.`, record, and time).

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
