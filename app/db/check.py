"""Check the database settings in `.env` without starting the API.

python -m app.db.check
"""

import asyncio
import sys

from app.db.session import check_connection, engine


async def main() -> int:
    try:
        info = await check_connection()
    except Exception as exc:
        print(f"Database connection FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print("Database connection OK")
    for key, value in info.items():
        print(f"  {key:<9} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
