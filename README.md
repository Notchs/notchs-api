# city-wrapped-api
This is the backend code of city wrapped web application.

Built with **FastAPI**, **SQLAlchemy 2.0 (async)**, **PostgreSQL** and **Alembic**.

## Project structure

```
city-wrapped-api/
├── app/
│   ├── main.py                 # App factory: middleware, exception handlers, routers
│   ├── api/
│   │   └── router.py           # Registers every module router under /api/v1
│   ├── core/                   # Cross-cutting concerns
│   │   ├── config.py           # Settings (env / .env)
│   │   ├── logging.py          # Logging setup
│   │   └── exceptions.py       # AppException hierarchy + global handlers
│   ├── db/
│   │   ├── base.py             # Declarative Base, mixins, `Entity` base model
│   │   ├── session.py          # Async engine + per-request session
│   │   └── models.py           # Model registry for Alembic autogenerate
│   ├── common/                 # Shared, reusable building blocks (DRY)
│   │   ├── schemas.py          # BaseSchema, TimestampSchema, EntitySchema, CustomResponse, PaginatedResponse, ErrorResponse
│   │   ├── dependencies.py     # DBSession, Pagination
│   │   ├── repository.py       # Generic async CRUD repository
│   │   └── service.py          # Generic CRUD service (get/list/create/update/delete)
│   ├── middleware/
│   │   └── request_context.py  # X-Request-ID + request timing logs
│   └── modules/                # One package per feature
│       └── health/
├── alembic/                    # Database migrations
├── tests/                      # Mirrors app/ layout
├── .devcontainer/              # VS Code Dev Container config
├── .env.example
├── Dockerfile                  # Multi-stage: dev / prod
├── docker-compose.yml          # Local stack: api (+ optional local postgres)
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
| `service.py`      | Business logic — subclass `BaseService[Model]`, raise `AppException`s |
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

This starts the API (with hot reload) against the database in `.env` (shared Neon DB).

- API docs: http://localhost:8000/docs (disabled in production)
- Liveness: `GET /api/v1/health` · Readiness (checks DB): `GET /api/v1/health/ready`

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

Full rules for the shared DB: [docs/WORKPLAN.md §0](docs/WORKPLAN.md#0-ground-rules-for-the-shared-postgresql-database).

## Docker images

The `Dockerfile` is multi-stage:

- `dev` — runtime + test/lint tooling; source is bind-mounted by `docker-compose.yml`.
- `prod` — runtime dependencies and application code only, runs as a non-root user:
  `docker build --target prod -t city-wrapped-api .`

## Branching strategy

- `main` — production-ready code.
- `development` — integration branch; all work branches are created from and merged back into it.
- `feature/<module-name>-module` — new features, e.g. `feature/auth-module`.
- `fix/<fix-name>` — bug fixes, e.g. `fix/login-token-expiry`.
