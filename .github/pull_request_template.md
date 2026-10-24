## What and why

<!-- One or two sentences. Which feature or fix? Link the matching web PR if there is one. -->

## Checklist (CLAUDE.md)

- [ ] Branched from the latest `development`; this PR goes **into `development`**
- [ ] CI is green (ruff, format, tests, single Alembic head, migrations up/down/up)
- [ ] Migrations tried in my own `dev_<name>` schema first; nothing run against `public` by hand
- [ ] New endpoints have tests; public endpoints have Turnstile, a rate limit and validation
- [ ] No secrets, `.env` files, dumps or personal data in code, tests, fixtures or logs
- [ ] No customer phone numbers returned to venue, public or admin-list endpoints
- [ ] Real data only: counts, ranks and queue positions come from real rows
- [ ] No business-initiated WhatsApp template messages (they cost money)

## Notes for Harsh and Abhishek

<!-- Anything that needs a decision. Merging into development migrates the shared DB. -->
