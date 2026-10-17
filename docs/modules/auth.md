# Auth Module (`auth`): Plan

Part of [WORKPLAN.md](../WORKPLAN.md). Covers how venues, admins and customers prove who they are, how a venue gets onto the platform in the first place, and how a venue's QR screen is paired.

**Code location:** `app/modules/auth/` (tokens, sessions, passwords, email codes) and `app/modules/venue_applications/` (apply and review)
**Depends on:** `core/`, `common/`, `venues` module (creates the venue on approval, owns the display endpoint), `EmailClient`, Redis (rate limits, email codes).
**Used by:** every router that needs `current_venue_user`, `current_admin`, `current_customer` or `current_device`.

**Scope of this plan:** venue apply (with email verification), approve, set password, login, refresh, logout, forgot password, change password, change email, QR screen pairing, admin login, and the customer login endpoint. The customer side of the WhatsApp `LOGIN` flow is described in [customers.md §4.3](customers.md#43-login-link-customer-side); the bot handler is built with the WhatsApp module.

---

## 1. Decisions made

| # | Decision |
|---|---|
| A1 | Venues join by **application**, not open signup. No account exists until an admin approves. |
| A2 | Venues log in with **email + password**. The first password is set through an emailed, single-use link. This replaces the spec's magic-link login for venues (the spec is a baseline, not final). |
| A3 | Admins also log in with email + password (WORKPLAN S1 changes from phone to email). |
| A4 | Customers never use a password, email or a signup form. Their identity is their WhatsApp number: they are created on their first scan and log in by sending `LOGIN` from that number, and the bot replies with a single-use link (free, because it is a reply inside the 24-hour window). Details in [customers.md §4.3](customers.md#43-login-link-customer-side). |
| A5 | Hosts: `notchs.com` (customers), `venues.notchs.com` (venues), `admin.notchs.com` (admin), `api.notchs.com` (this backend). `notchs.com` has a "For venues" button to `venues.notchs.com/apply`. |
| A6 | Free email provider: **Resend** (3,000 emails/month, 100/day), behind an `EmailClient` class. Local development uses dry-run mode and needs no provider. |
| A7 | **One login per venue.** There are no staff users and no roles. The spec's `venue_users.role` column is dropped. The dashboard is for the QR screen, insights, settings and rewards. If a venue needs a second person later, staff can be added without breaking anything. |
| A8 | **Email addresses are verified with a 6-digit code (OTP)** before they are trusted: when applying, and when a venue changes its email. |
| A9 | **Changing the password does not log the user out.** The session they are using stays valid. Sessions on other devices are ended. |
| A10 | Each Next.js app proxies `/api/*` to the backend, so cookies live on the app's own host and CORS is not needed (§5). |
| A11 | New applications appear in the admin queue, plus an optional email to `ADMIN_NOTIFY_EMAIL`. |
| A12 | An approved venue can take scans immediately. The admin vetted it at approval. |

---

## 2. Venue flows

```
Apply            venues.notchs.com/apply
  1. verify      POST /venue-applications/email/send-code  {email, turnstile_token}
                 a 6-digit code is emailed
                 POST /venue-applications/email/verify     {email, code}
                 → {verification_token}  (30 min, single use)
  2. submit      POST /venue-applications  {venue details, verification_token}
                 row created, status = pending
                 optional email to the admin: "new application"

Review           admin.notchs.com            GET  /admin/venue-applications
                                             POST /admin/venue-applications/{id}/approve
                                             POST /admin/venue-applications/{id}/reject

On approve       ONE transaction: create the venue + its venue_user (no password yet)
(after commit)   + a set_password token (72 h). Send "Welcome, set your password" email.
On reject        Status = rejected, optional reason, email the applicant.

Set password     link: venues.notchs.com/set-password?token=...
                 page does POST /venue/auth/set-password  {token, password}
                 token consumed, password stored, session cookies issued, redirect to /dashboard

Login            POST /venue/auth/login {email, password} → cookies → /dashboard
Forgot password  POST /venue/auth/forgot-password {email}  → always 202
                 sends a password_reset link if the email belongs to an active venue user
                 (same page and endpoint as set-password)

Change password  POST /venue/auth/change-password {current_password, new_password}
                 stays logged in on this device, other devices are logged out

Change email     POST /venue/me/email/send-code {new_email, current_password}
                 a 6-digit code goes to the NEW address
                 POST /venue/me/email/verify {code}
                 email updated, a notice goes to the OLD address, session stays
```

A link always opens a page, and the page sends a `POST`. Nothing changes on `GET`, because email security scanners prefetch links and would burn a single-use token.

---

## 3. Email verification codes (OTP)

Used by the application form and by change-email. Not stored in Postgres.

| Rule | Value |
|---|---|
| Code | 6 digits, from `secrets`. Only its SHA-256 hash is kept. |
| Storage | Redis `otp:{purpose}:{email}` with the hash and an attempt counter. `purpose` is `application` or `email_change`. |
| Lifetime | 10 minutes |
| Wrong attempts | 5, then the code is deleted and a new one must be requested |
| Resend | 60 s cooldown, 5 codes per hour per email, 10 per hour per IP |
| After success | Application: a `verification_token` (Redis `emv:{token}` → email, 30 min, single use) that the submit call must carry, and the submitted email must match it. Change email: the email is updated in the same call. |

- A new code replaces the old one. Codes are compared in constant time.
- `send-code` always answers `202`, whether or not the address is already in use. An address that already belongs to a venue is refused at submit (`applications.duplicate`) or silently skipped for change-email, so the endpoint cannot be used to find out who is registered.
- `venue_applications.email_verified_at` records when the code was accepted.
- In development with `EMAIL_DRY_RUN=true` the code is written to the log.

---

## 4. Tables

Conventions as in [customers.md §2](customers.md#2-tables). `created_at` and `updated_at` come from `TimestampMixin`. Primary keys are UUIDv7. Emails are stored **lowercase** and compared lowercase.

### 4.1 `venue_applications` (new)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | uuid | | PK |
| `application_code` | text | | Public code, prefix `APP` (WORKPLAN §1.1) |
| `venue_name` | text | | 2–80 chars |
| `category` | text | | `cafe`, `restaurant`, `bakery`, `bar`, `other` (validated list in code) |
| `address` | text | | |
| `city_id` | uuid | | FK `cities` |
| `area_id` | uuid | yes | FK `areas`. Applicant picks from the seeded list. |
| `contact_name` | text | | |
| `email` | text | | Lowercase. Becomes the venue's login. |
| `email_verified_at` | timestamptz | | Set from the verification step (§3). An application cannot exist without it. |
| `phone_e164` | text | | Contact number |
| `notes` | text | yes | Anything the applicant wants to add |
| `status` | enum `application_status` (`pending`, `approved`, `rejected`) | | Default `pending` |
| `reviewed_by` | uuid | yes | Admin user id, no FK needed (audited) |
| `reviewed_at` | timestamptz | yes | |
| `rejection_reason` | text | yes | Shown to the admin, and optionally in the email |
| `venue_id` | uuid | yes | FK `venues`. Set on approval. |

Indexes: unique (`application_code`), unique (`email`) WHERE `status = 'pending'` (one open application per email), (`status`, `created_at`) for the admin queue.
Rejected applicants may apply again later.

### 4.2 `venue_users` (changed from the spec)

One row per venue: the venue's login.

| Column | Type | Null | Notes |
|---|---|---|---|
| `venue_id` | uuid | | FK `venues`. **Unique** (A7: one login per venue). |
| `email` | text | | Unique, lowercase. **New** (replaces phone as the login identity). |
| `password_hash` | text | yes | argon2id. `NULL` until the password is set. |
| `is_active` | boolean | | An admin can deactivate. Inactive users cannot log in and their sessions are revoked. |
| `failed_login_count` | integer | | Default 0. Reset on success. |
| `locked_until` | timestamptz | yes | Set after too many failures (§6) |
| `last_login_at` | timestamptz | yes | |

**Dropped from the spec:** `role` (`owner`/`staff`). Other columns (`name`, ids, timestamps) are as in the spec.

### 4.3 `venue_devices` (spec table, used for QR screens)

| Column | Type | Null | Notes |
|---|---|---|---|
| `venue_id` | uuid | | FK `venues` |
| `name` | text | | Given when pairing, e.g. "Counter tablet" |
| `token_hash` | text | | Unique. SHA-256 of the long-lived device token. |
| `last_seen_at` | timestamptz | yes | Updated at most once a minute. Feeds the "QR screen is running" signal. |
| `revoked_at` | timestamptz | yes | Set when the venue removes the device |

### 4.4 `auth_tokens` (changes to [customers.md §2.2](customers.md#22-auth_tokens-owned-by-auth-listed-here-because-customer-login-depends-on-it))

New purposes: `set_password` (72 h, from approval) and `password_reset` (30 min). Both single use, stored as SHA-256 hashes, `subject_type = venue_user` (or `admin`).

```
auth_subject_type   : customer, venue_user, admin
auth_token_purpose  : login_link, device_pairing, refresh, set_password, password_reset
```

Since migration 0001 is not written yet, these go straight into it (WORKPLAN S10 only matters for enums that already exist in a deployed database).

---

## 5. Sessions

| Token | Lifetime | Stored | Cookie |
|---|---|---|---|
| Access (JWT: `sub`, `typ=venue_user`, `venue_id`) | 15 min | not stored | `nx_venue_at`, `Path=/` |
| Refresh (random, 256 bit) | 30 days | `auth_tokens` hash, `family_id` | `nx_venue_rt`, `Path=/api/v1/auth` |

Admins and customers use the same pair with their own names (`nx_admin_*`, `nx_customer_*`).

- All cookies: `HttpOnly`, `Secure` (off only when `ENV=development`), `SameSite=Lax`, no `Domain` attribute, so each host keeps its own.
- **Refresh rotation:** each `POST /auth/refresh` marks the old token used and issues a new one in the same `family_id`. If an already-used token shows up again, the whole family is revoked (theft signal) and the user must log in again.
- **Logout** revokes the family and clears both cookies.
- **Change password** (A9) keeps the family of the session that made the request and revokes every other family for that user.
- **Password reset** (forgot password) revokes every family. The reset call itself issues a fresh session, so the user is not sent back to the login page.
- **Change email** keeps all sessions.
- **Deactivation** by an admin revokes every family.
- `current_venue_user` dependency: verifies the JWT, loads the row, rejects (401) if the user is missing or inactive.

### Cookies and the frontend

Each Next.js app **proxies** `/api/*` to the backend (a rewrite), so the browser only ever talks to its own host (A10). Cookies are set on `venues.notchs.com` itself, Next.js middleware can read them to guard pages, and CORS is not needed.

---

## 6. Security rules

| Area | Rule |
|---|---|
| Password hashing | argon2id (`argon2-cffi`), library defaults. Re-hash on login if parameters change. |
| Password policy | 10–128 characters, not equal to the email. No composition rules. |
| Login failures | Redis `rl:login:{email}:{ip}`: 5 failures in 15 min → `locked_until` 15 min. Same generic error for wrong email, wrong password and locked: `auth.invalid_credentials`. A dummy hash is verified when the email is unknown so response time does not reveal it. |
| Forgot password | Always `202`, same body, whether or not the email exists. Limit 3 per hour per email and 10 per hour per IP. |
| Sensitive changes | Change password and change email both require the current password. |
| Application form | Cloudflare Turnstile (free) verified server side on `send-code`. Email verified by code (§3). 5 applications per IP per day. Disposable email domains blocked. |
| Tokens | Random, 256 bit, SHA-256 stored, single use, `used_at` set in the same transaction as the password change. Expired or used → `auth.token_invalid`. |
| Enumeration | Login, forgot-password and `send-code` never reveal whether an email is registered. |
| Audit | `venue_application.approved/rejected`, `venue_user.password_set`, `venue_user.password_changed`, `venue_user.email_changed` (old and new address), `venue_user.login_failed_locked`, `venue_user.deactivated`, `venue_device.paired/revoked` via `audit.record()`. |

---

## 7. QR screen pairing

The QR with the rotating code runs on a tablet or phone at the counter. That device must not hold the venue's full login, so it gets its own limited token that can only show the QR.

```
Dashboard        POST /venue/devices/pairing-links
                 → a device_pairing token (10 min, single use), shown as a link and a QR:
                   venues.notchs.com/display/pair?token=...

On the tablet    open the link (or scan the QR)
                 page does POST /display/pair {token, name}
                 → venue_devices row created, long-lived device token set as cookie nx_device
                 → redirect to venues.notchs.com/display

Display page     calls the display endpoint with the device cookie (current_device)
Remove a screen  DELETE /venue/devices/{id}  → revoked_at set, the screen stops on its next call
```

- The device token is random (256 bit), stored only as a hash, and does not expire. It ends when the venue revokes it or an admin deactivates the venue.
- Cookie `nx_device`: `HttpOnly`, `Secure`, `SameSite=Lax`, 1 year, renewed on use.
- At most 3 active devices per venue.
- `current_device` is a separate dependency from `current_venue_user`. A device token is accepted only by display routes and never by dashboard routes.
- The display endpoint itself (`GET /venue/display/config`, what it returns, how the code rotates) belongs to the venues module, WORKPLAN 2.3. Proposal for that work: the server returns the current code and the seconds left, so the TOTP secret never leaves the backend.

---

## 8. API

All routes under `/api/v1`. Errors use `{"success": false, "error": {"code", "message"}}`.

| Method | Path | Auth | Body | Result |
|---|---|---|---|---|
| POST | `/venue-applications/email/send-code` | public + Turnstile | `{email, turnstile_token}` | 202 |
| POST | `/venue-applications/email/verify` | public | `{email, code}` | 200 `{verification_token}` |
| POST | `/venue-applications` | verification token | `ApplicationIn` + `verification_token` | 201 `{application_code}` |
| GET | `/admin/venue-applications` | admin | `?status=&cursor=` | `Page[ApplicationOut]` |
| GET | `/admin/venue-applications/{id}` | admin | | `ApplicationOut` (id or `APP-...` code) |
| POST | `/admin/venue-applications/{id}/approve` | admin | | 200, venue + login created, email sent |
| POST | `/admin/venue-applications/{id}/reject` | admin | `{reason?, notify: bool}` | 200 |
| POST | `/venue/auth/set-password` | token in body | `{token, password}` | 200 + cookies |
| POST | `/venue/auth/login` | public | `{email, password}` | 200 + cookies, `VenueUserOut` |
| POST | `/venue/auth/forgot-password` | public + Turnstile | `{email}` | 202 |
| POST | `/venue/auth/change-password` | venue user | `{current_password, new_password}` | 204 |
| GET | `/venue/me` | venue user | | `VenueUserOut` (name, email, venue summary) |
| POST | `/venue/me/email/send-code` | venue user | `{new_email, current_password}` | 202 |
| POST | `/venue/me/email/verify` | venue user | `{code}` | 200 `VenueUserOut` |
| POST | `/venue/devices/pairing-links` | venue user | | 201 `{url, expires_at}` |
| GET | `/venue/devices` | venue user | | list of devices with `last_seen_at` |
| DELETE | `/venue/devices/{id}` | venue user | | 204 |
| POST | `/display/pair` | token in body | `{token, name}` | 200 + `nx_device` cookie |
| POST | `/admin/auth/login` | public | `{email, password}` | 200 + admin cookies |
| POST | `/auth/customer/consume` | token in body | `{token}` | 200 + customer cookies. Token is the `login_link` the WhatsApp bot sent. |
| POST | `/auth/refresh` | refresh cookie | | 200 + rotated cookies |
| POST | `/auth/logout` | refresh cookie | | 204 |

`/auth/refresh` and `/auth/logout` are shared across subject types: the refresh token's own `subject_type` decides which cookies to rotate.

**Error codes:** `auth.invalid_credentials` 401, `auth.token_invalid` 400, `auth.code_invalid` 400 (wrong or expired email code), `auth.rate_limited` 429, `auth.account_inactive` 403, `auth.password_invalid` 422 (policy), `applications.duplicate` 409, `applications.invalid_state` 409 (approving something not pending), `devices.limit_reached` 409, `captcha.failed` 400.

---

## 9. Files

```
app/modules/auth/
    models.py        # AuthToken (+ enums)
    tokens.py        # create/hash/consume tokens, refresh rotation, reuse detection
    passwords.py     # hash, verify, policy check (pure + argon2)
    otp.py           # email codes: create, verify, attempt and resend limits (Redis)
    jwt.py           # encode/decode access tokens
    cookies.py       # set/clear session and device cookies
    service.py       # login, refresh, logout, set/forgot/change password, change email, consume customer link
    devices.py       # pairing link, pair, list, revoke
    router.py        # /venue/auth/*, /venue/me/email/*, /venue/devices/*, /display/pair,
                     # /admin/auth/*, /auth/customer/consume, /auth/refresh, /auth/logout
    dependencies.py  # current_venue_user, current_admin, current_customer, current_device
app/modules/venue_applications/
    models.py  schemas.py  service.py  router.py   # verify email, apply, admin review
app/clients/
    email.py         # EmailClient (Resend, dry-run), templates in app/clients/email_templates/
    turnstile.py     # TurnstileClient.verify(token, ip)
```

Settings added: `JWT_SECRET`, `ACCESS_TOKEN_TTL_MIN`, `REFRESH_TOKEN_TTL_DAYS`, `EMAIL_DRY_RUN`, `RESEND_API_KEY`, `EMAIL_FROM`, `TURNSTILE_SECRET`, `TURNSTILE_DISABLED` (dev only), `CUSTOMER_APP_URL`, `VENUE_APP_URL`, `ADMIN_APP_URL`, `ADMIN_NOTIFY_EMAIL` (optional).

Emails sent: verification code, new application (to admin), welcome + set password, application rejected, password reset, password changed notice, email changed notice (to the old address).

---

## 10. Tests

**Unit:** password policy, hash and verify, token creation and hashing, email code creation and verification, JWT expiry and tampering, email normalisation, disposable-domain check.

**Integration (real Postgres schema, Redis, mocked `EmailClient` and `TurnstileClient`):**
- Email code: correct code works once, wrong code 5 times kills it, expired code fails, resend cooldown and hourly limits apply.
- Apply: submit without a verification token fails, token for a different email fails, token is single use. Duplicate pending application for the same email → 409. Reapply after rejection works.
- Approve creates the venue and its login in one transaction, and the email carries a valid token. Approving twice → 409.
- Set password: valid token works once, reuse and expired tokens fail, cookies are issued, password policy enforced.
- Login: success, wrong password, unknown email (same error and similar timing), inactive user, 5 failures lock the account.
- Refresh: rotation works, reusing an old token revokes the family.
- Forgot password: identical response for known and unknown emails, reset revokes existing sessions.
- Change password: wrong current password fails, the current session keeps working, a second session on another device is logged out.
- Change email: wrong password fails, code goes to the new address only, the old address gets a notice, login works with the new email and not the old one, an email already in use is not taken over.
- Devices: pairing token works once and expires, device cookie reaches the display route but not dashboard routes, revoked device is refused, 4th device → 409.
- Customer link: `POST /auth/customer/consume` works once, expired and reused tokens fail.
- Rate limits: send-code, login, forgot-password.

---

## 11. Tasks (in order)

| # | Task | Size | Depends on |
|---|---|---|---|
| A-1 | Models and enums for `auth_tokens`, `venue_applications`, `venue_users`, `venue_devices`, `admin_users`, in migration 0001 | M | WORKPLAN 1.6 |
| A-2 | `passwords.py`, `jwt.py`, `tokens.py` + unit tests | M | – (can start now) |
| A-3 | `EmailClient` (Resend + dry-run) and `TurnstileClient`, with config | M | 1.2 |
| A-4 | `otp.py`: email codes + unit tests | S | Redis (1.2) |
| A-5 | Venue applications: send-code, verify, submit | M | A-1, A-3, A-4 |
| A-6 | Login, refresh, logout, cookies, `current_venue_user`, `current_customer`, `POST /auth/customer/consume` | M | A-2 |
| A-7 | Set password and forgot password | M | A-3, A-6 |
| A-8 | Change password and change email | M | A-4, A-6 |
| A-9 | Admin login: `admin_users` (email + password), CLI `python -m app.cli create-admin` for the first admin, `current_admin` | M | A-6 |
| A-10 | Admin: list, approve, reject (approve calls `venues.service.create_from_application`) | M | A-9, venues module |
| A-11 | Device pairing: pairing link, pair, list, revoke, `current_device` | M | A-6, venues module |
| A-12 | Rate limits and lockout | S | A-6 |
| A-13 | Integration tests from §10 | M | all |

The bot's `LOGIN` handler is a task in the WhatsApp and customers work (customers.md C8).

---

## 12. Open questions

None at the moment. Not planned for the pilot, to be added if needed: staff users, a "logged in devices" list, moving a customer account to a new phone number, an admin changing a venue's email by hand (possible directly in the database until there is a screen for it).
