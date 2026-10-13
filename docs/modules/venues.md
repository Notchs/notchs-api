# Venues Module (`venues`): Plan

Part of [WORKPLAN.md](../WORKPLAN.md). A venue is a cafe, restaurant, bar or other place where customers collect notches.

**Code location:** `app/modules/venues/`, with `app/modules/locations/` (cities, areas) and `app/modules/admin_users/` for the tables it points at.
**Depends on:** `core/`, `common/`, `db/`.
**Used by:** scans, visits, rewards, insights, auth (venue login and applications).

---

## 1. What exists now

| Piece | State |
|---|---|
| Tables `cities`, `areas`, `admin_users`, `venues` | ✅ Migration `0001` |
| Models, enums, constraints | ✅ |
| Request and response schemas, opening-hours validation | ✅ |
| Repository (lookups, filtered list, soft delete aware) | ✅ |
| Service (create, update, status, soft delete, slug generation) | ✅ |
| HTTP routes | Not yet. Admin routes need admin login (auth module). See §6. |
| Seed data (Ahmedabad, areas, test venues) | Not yet (WORKPLAN 1.7) |

---

## 2. `venues` table

Taken from the spec sheet. **Source**: `spec` = as in the sheet, `added` = not in the sheet.

| Column | Type | Null | Default | Source | Notes |
|---|---|---|---|---|---|
| `id` | uuid | | | spec | PK. UUIDv7 generated in the app (WORKPLAN §1.1), not `gen_random_uuid()`. |
| `venue_code` | varchar(12) | | | added | Unique public code `VEN-XXXXXXXX` (WORKPLAN §1.1) |
| `city_id` | uuid | | | spec | FK `cities.id`, indexed |
| `area_id` | uuid | yes | | spec | FK `areas.id`, indexed. Must be an area of the same city (checked in the service). |
| `name` | varchar(120) | | | spec | e.g. Kahwa House |
| `slug` | varchar(120) | | | spec | Unique per city. URL: `/ahmedabad/v/kahwa-house` |
| `category` | varchar(16) | | | spec | `cafe`, `restaurant`, `bar`, `hotel`, `turf_gym`, `other`. Indexed. |
| `address` | text | yes | | spec | |
| `latitude`, `longitude` | numeric(9,6) | yes | | spec | Both or neither |
| `geofence_radius_m` | integer | | 150 | spec | Only used if geofencing is switched on later |
| `description` | varchar(160) | yes | | spec | One line on the venue page |
| `photo_s3_key` | varchar(255) | yes | | spec | S3 key, not a full URL |
| `opening_hours` | jsonb | yes | | spec | `{"mon": [["08:00","23:00"]], ...}` in IST (§4) |
| `status` | varchar(14) | | `pending` | spec | `pending`, `signed`, `counter_live`, `paused`, `closed`. Indexed. |
| `founding_venue_no` | integer | yes | | spec | Unique, 1–30 |
| `first_notch_spots` | integer | | 50 | spec | First Notch badge limit for this venue |
| `free_until` | date | yes | | spec | End of the free months |
| `monthly_fee_paise` | bigint | yes | | spec | Flat fee after the free months. Amount still open. |
| `signed_by_admin_id` | uuid | yes | | spec | FK `admin_users.id` |
| `created_at`, `updated_at` | timestamptz | | `now()` | spec | UTC |
| `deleted_at` | timestamptz | yes | | spec | Soft delete |

**Constraints added on top of the sheet**, so bad data is refused by the database and not only by the API:

| Name | Rule |
|---|---|
| `ck_venues_category_valid`, `ck_venues_status_valid` | Value must be in the list above |
| `ck_venues_slug_format` | Lowercase words joined by single hyphens |
| `ck_venues_latitude_range`, `ck_venues_longitude_range` | −90..90 and −180..180 |
| `ck_venues_coordinates_paired` | Latitude and longitude are both set or both empty |
| `ck_venues_geofence_radius_positive` | > 0 |
| `ck_venues_founding_venue_no_range` | 1–30 |
| `ck_venues_first_notch_spots_non_negative`, `ck_venues_monthly_fee_paise_non_negative` | ≥ 0 |

`category` and `status` are text with a CHECK, as the sheet has them, not Postgres enums. Adding a value later is one ordinary migration (compare WORKPLAN S10).

---

## 3. Supporting tables (assumed, please confirm)

The spec rows for these three tables were not available when this was built, so they hold only what `venues` and the WORKPLAN already need. **Send the spec rows and they will be reconciled before this branch is merged.**

| Table | Columns |
|---|---|
| `cities` | `id`, `name` varchar(80), `slug` varchar(80) unique, timestamps |
| `areas` | `id`, `city_id` FK (indexed), `name` varchar(80), `slug` varchar(80), unique (`city_id`, `slug`), timestamps |
| `admin_users` | `id`, `admin_user_code` (`ADM-...`), `name` varchar(120), `email` varchar(254) unique and lowercase, `password_hash` varchar(255) nullable, `is_active` default true, timestamps |

`admin_users` follows WORKPLAN S1. Its login is built in the auth module.

---

## 4. Rules

**Slug**
- Generated from the name when not given: `Kahwa House` → `kahwa-house`. Accents are removed, and anything that is not a letter or digit becomes a hyphen.
- A name with no Latin letters (for example only Gujarati script) becomes `venue`.
- If the slug is taken in that city, a number is added: `kahwa-house-2`, `kahwa-house-3`.
- Renaming a venue does **not** change its slug, so shared links keep working. An admin can set a new slug explicitly.
- A soft-deleted venue keeps its slug reserved, so an old link never opens a different venue.

**Opening hours**
- Keys are `mon` to `sun`. Each day has a list of `["HH:MM", "HH:MM"]` pairs, at most 4 per day.
- A missing or empty day means closed.
- A closing time earlier than the opening time means the venue closes after midnight (`["18:00", "01:30"]`).

**Status**
- New venues start as `pending`.
- The first time a venue is marked `signed`, the admin doing it is stored in `signed_by_admin_id`. It is not overwritten later.
- No transition rules are enforced yet (see §7).

**Delete** is a soft delete (`deleted_at`). Deleted venues are hidden from every lookup and list.

**Error codes:** `venues.not_found` 404, `venues.slug_taken` 409, `venues.founding_venue_no_taken` 409, `venues.location_invalid` 400.

---

## 5. Files

```
app/modules/locations/models.py     # City, Area
app/modules/admin_users/models.py   # AdminUser
app/modules/venues/
    models.py        # Venue, VenueCategory, VenueStatus
    schemas.py       # VenueCreate, VenueUpdate, VenueStatusUpdate, VenueOut, VenuePublicOut, OpeningHours
    slug.py          # slugify, with_suffix
    repository.py    # VenueRepository
    service.py       # VenueService
    exceptions.py    # module error codes
alembic/versions/20261014_0241_0001_cities_areas_admin_users_venues.py
```

---

## 6. Next steps

| # | Task | Depends on |
|---|---|---|
| V1 | Confirm or correct `cities`, `areas`, `admin_users` against the spec (§3) | spec rows |
| V2 | Admin routes: create, list, get, update, set status, delete | admin login (auth) |
| V3 | Public route: `GET /cities/{city_slug}/venues/{venue_slug}` returning `VenuePublicOut` | decision Q2 |
| V4 | Seed: Ahmedabad, 3 areas, about 6 test venues | WORKPLAN 1.7 |
| V5 | Create the venue from an approved application (`create_from_application`) | auth module |
| V6 | Integration tests on a real schema in CI | test DB fixtures (WORKPLAN §0.8) |

---

## 7. Open questions

| # | Question | Note |
|---|---|---|
| Q1 | Where do the scan code (`KAAFI-SBR`) and the TOTP secret live? | WORKPLAN 2.1 and §1.1 describe `venues.code` and an encrypted TOTP secret, but the spec sheet's `venues` table has neither. They may be in another table in the sheet. Not built until this is clear. |
| Q2 | Which statuses are visible to the public? | Probably only `counter_live`. Needed before the public route. |
| Q3 | Which status changes are allowed? | For example can `closed` go back to `counter_live`? Nothing is enforced yet. |
| Q4 | How is `founding_venue_no` given out? | Today an admin types it. It could be assigned automatically to the first 30 signed venues. |
| Q5 | Does `opening_hours` need more than IST? | Fine while every venue is in India. |
