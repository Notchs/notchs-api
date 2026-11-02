"""The admin panel over HTTP (G1-G6)."""

from datetime import UTC, datetime, timedelta

from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.main import app
from app.modules.admin_users.models import AdminRole
from app.modules.audit.models import AuditLog
from app.modules.jobs.models import JobName, RunTrigger
from app.modules.jobs.runner import SessionFactory, claim
from app.modules.notches.checkin import CheckInService
from app.modules.notches.codes import ScanMessage
from app.modules.users.models import User, UserStatus
from tests import factories as f


async def _shared_code(db: AsyncSession, redis: FakeAsyncRedis):
    """Two numbers send the same code: both Notchs flagged, one signal."""
    city = await f.city(db)
    venue = await f.venue(db, city, name="Kahwa House")
    await f.counter(db, venue)
    now = datetime.now(UTC)
    scan = ScanMessage(venue.venue_code, f.code_at(now))
    service = CheckInService(db, redis)
    first = await service.scan(await f.user(db), scan, scanned_at=now)
    second = await service.scan(await f.user(db), scan, scanned_at=now + timedelta(seconds=3))
    await db.flush()
    return venue, first.notch, second.notch


async def test_proof_review_queue(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, first, second = await _shared_code(db, redis)
    reviewer = await f.admin(db, role=AdminRole.REVIEWER)
    api.cookies = f.login(reviewer)

    queue = (await api.get("/admin/notches")).json()["data"]
    assert [n["notch_code"] for n in queue["items"]] == [first.notch_code, second.notch_code]
    assert queue["items"][0]["flags"] == ["shared_code"]
    assert queue["items"][0]["customer_code"].startswith("USR-")

    detail = (await api.get(f"/admin/notches/{first.notch_code}")).json()["data"]
    assert [n["notch_code"] for n in detail["same_code"]] == [second.notch_code]

    # Reviewers decide proof review (A / R).
    approved = await api.post(f"/admin/notches/{first.notch_code}/approve", json={})
    assert approved.json()["data"]["status"] == "verified"
    rejected = await api.post(
        f"/admin/notches/{second.notch_code}/reject", json={"note": "Screenshot"}
    )
    assert rejected.json()["data"]["reviewed_by"] == reviewer.name
    assert (await api.get("/admin/notches")).json()["data"]["total"] == 0

    actions = list(await db.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))
    assert actions == ["notch.approved", "notch.rejected"]


async def test_fraud_signals(api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis) -> None:
    await _shared_code(db, redis)
    api.cookies = f.login(await f.admin(db))

    (signal,) = (await api.get("/admin/fraud-signals")).json()["data"]["items"]
    assert signal["kind"] == "shared_code" and signal["details"]["numbers"] == 2
    resolved = await api.post(
        f"/admin/fraud-signals/{signal['id']}/resolve",
        json={"status": "dismissed", "note": "Friends together"},
    )
    assert resolved.json()["data"]["status"] == "dismissed"
    assert (await api.get("/admin/fraud-signals")).json()["data"]["total"] == 0


async def test_venues(api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis) -> None:
    venue, *_ = await _shared_code(db, redis)
    reviewer = await f.admin(db, role=AdminRole.REVIEWER)
    api.cookies = f.login(reviewer)

    (row,) = (await api.get("/admin/venues?q=kahwa")).json()["data"]["items"]
    assert row["notches_7d"] == 2 and row["counter_online"] is True
    detail = (await api.get(f"/admin/venues/{venue.venue_code}")).json()["data"]
    assert detail["notches_total"] == 2
    # Reviewers can look but not change.
    assert (
        await api.post(f"/admin/venues/{venue.venue_code}/status", json={"status": "paused"})
    ).status_code == 403

    api.cookies = f.login(await f.admin(db))
    paused = await api.post(f"/admin/venues/{venue.venue_code}/status", json={"status": "paused"})
    assert paused.json()["data"]["status"] == "paused"
    edited = await api.patch(f"/admin/venues/{venue.venue_code}", json={"first_notch_spots": 30})
    assert edited.json()["data"]["first_notch_spots"] == 30


async def test_users_lookup_block_and_delete(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, first, _ = await _shared_code(db, redis)
    api.cookies = f.login(await f.admin(db))
    user = await db.get(User, first.user_id)

    found = (await api.get(f"/admin/users?q={user.phone_e164[-10:]}")).json()["data"]["items"]
    assert [u["user_code"] for u in found] == [user.user_code]
    assert found[0]["phone"] == f"•••• {user.phone_e164[-4:]}"

    detail = (await api.get(f"/admin/users/{user.user_code}")).json()["data"]
    assert detail["notches_total"] == 1 and detail["visits"][0]["venue_name"] == "Kahwa House"
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "user.looked_up"))

    assert (await api.post(f"/admin/users/{user.user_code}/block")).json()["data"][
        "status"
    ] == "blocked"
    assert (await api.post(f"/admin/users/{user.user_code}/unblock")).json()["data"][
        "status"
    ] == "active"
    deleted = await api.post(f"/admin/users/{user.user_code}/delete")
    assert deleted.json()["data"]["status"] == "confirmed"
    await db.refresh(user)
    assert user.status == UserStatus.DELETED
    (row,) = (await api.get("/admin/user-deletions")).json()["data"]["items"]
    assert row["source"] == "admin"


async def test_overview_jobs_and_audit_log(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await _shared_code(db, redis)
    api.cookies = f.login(await f.admin(db))

    overview = (await api.get("/admin/overview")).json()["data"]
    assert overview["flagged_notches"] == 2 and overview["open_fraud_signals"] == 1
    assert overview["venues_by_status"] == {"counter_live": 1}

    assert (await api.get("/admin/jobs")).json()["data"]["total"] == 0
    assert (await api.get("/admin/audit-log")).json()["data"]["total"] == 0


async def test_double_clicks_are_audited_once(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue, first, _ = await _shared_code(db, redis)
    api.cookies = f.login(await f.admin(db))
    user = await db.get(User, first.user_id)

    for _ in range(2):
        await api.post(f"/admin/users/{user.user_code}/block")
        await api.post(f"/admin/venues/{venue.venue_code}/status", json={"status": "paused"})
    assert (await api.post(f"/admin/users/{user.user_code}/delete")).status_code == 200
    assert (await api.post(f"/admin/users/{user.user_code}/delete")).status_code == 409

    actions = list(await db.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))
    assert actions.count("user.blocked") == 1 and actions.count("venue.paused") == 1
    filtered = await api.get(f"/admin/audit-log?target_type=venue&target_id={venue.id}")
    assert [e["action"] for e in filtered.json()["data"]["items"]] == ["venue.paused"]


async def test_running_a_job_from_admin(
    api: AsyncClient, db: AsyncSession, session_factory: SessionFactory
) -> None:
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    reviewer = await f.admin(db, role=AdminRole.REVIEWER)
    api.cookies = f.login(reviewer)
    assert (
        await api.post("/admin/jobs/notch_cards/run", json={"period": "2026-09"})
    ).status_code == 403

    api.cookies = f.login(await f.admin(db))
    started = await api.post("/admin/jobs/notch_cards/run", json={"period": "2026-09"})
    assert started.status_code == 202
    runs = (await api.get("/admin/jobs")).json()["data"]["items"]
    assert [(r["job"], r["period"], r["status"], r["trigger"]) for r in runs] == [
        ("notch_cards", "2026-09", "succeeded", "admin")
    ]
    assert (
        await api.post("/admin/jobs/notch_cards/run", json={"period": "13-2026"})
    ).status_code == 422


async def test_a_running_job_cannot_be_started_twice(
    api: AsyncClient, db: AsyncSession, session_factory: SessionFactory
) -> None:
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    api.cookies = f.login(await f.admin(db))
    await claim(session_factory, JobName.NOTCH_CARDS, "2026-08", trigger=RunTrigger.SCHEDULER)

    second = await api.post("/admin/jobs/notch_cards/run", json={"period": "2026-08"})
    assert second.status_code == 409 and second.json()["error"]["code"] == "jobs.running"
