"""Command-line tasks. Run inside the api container (or with the same .env):

    python -m app.cli create-admin --email you@notchs.com --name "Your Name" [--role admin]
    python -m app.cli admin-link --email you@notchs.com     # new set-password link
    python -m app.cli seed                                  # Ahmedabad + its areas, idempotent

They use DATABASE_URL and DB_SCHEMA from .env, like the API. Links are printed and emailed
(EMAIL_DRY_RUN=true only logs the email).
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.clients.email import EmailClient
from app.core.logging import setup_logging
from app.core.redis import redis_client
from app.db.seed import seed
from app.db.session import AsyncSessionLocal, engine
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.auth.emails import check_email


async def _send_invite(admin: AdminUser, auth: AdminAuthService) -> str:
    message = await auth.invite_email(admin)
    await EmailClient().send(message)
    return message.text


async def create_admin(email: str, name: str, role: AdminRole) -> int:
    email = check_email(email)
    async with AsyncSessionLocal() as session:
        if await session.scalar(select(AdminUser).where(AdminUser.email == email)):
            print(f"An admin with {email} already exists. Use admin-link for a new link.")
            return 1
        admin = AdminUser(email=email, name=name.strip(), role=role)
        session.add(admin)
        await session.flush()
        text = await _send_invite(admin, AdminAuthService(session, redis_client))
        await session.commit()
    print(f"Created {role.value} {admin.admin_user_code} <{email}>.\n\n{text}")
    return 0


async def admin_link(email: str) -> int:
    email = check_email(email)
    async with AsyncSessionLocal() as session:
        admin = await session.scalar(select(AdminUser).where(AdminUser.email == email))
        if admin is None:
            print(f"No admin with {email}. Use create-admin.")
            return 1
        text = await _send_invite(admin, AdminAuthService(session, redis_client))
        await session.commit()
    print(f"New set-password link for {email} (older links stop working).\n\n{text}")
    return 0


async def run_seed() -> int:
    async with AsyncSessionLocal() as session:
        counts = await seed(session)
        await session.commit()
    print(f"Seeded {counts['cities']} city and {counts['areas']} areas (safe to run again).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser(
        "create-admin", help="Create an admin and send a set-password link"
    )
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument(
        "--role", choices=[r.value for r in AdminRole], default=AdminRole.ADMIN.value
    )
    link = commands.add_parser("admin-link", help="Send an existing admin a new set-password link")
    link.add_argument("--email", required=True)
    commands.add_parser("seed", help="Add Ahmedabad and its areas (safe to repeat)")

    args = parser.parse_args(argv)
    setup_logging()

    async def run() -> int:
        try:
            if args.command == "create-admin":
                return await create_admin(args.email, args.name, AdminRole(args.role))
            if args.command == "admin-link":
                return await admin_link(args.email)
            return await run_seed()
        except ValueError as exc:  # e.g. a malformed email
            print(f"Error: {exc}")
            return 2
        finally:
            await redis_client.aclose()
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
