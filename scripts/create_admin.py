"""Create or update a WealthOS admin user.

Usage (from the repository root or anywhere):

    backend/.venv/Scripts/python.exe scripts/create_admin.py \
        --email admin@example.com --username admin --password <secret>

Each option falls back to the corresponding ADMIN_EMAIL / ADMIN_USERNAME /
ADMIN_PASSWORD environment variable. The target database comes from
DATABASE_URL (settings / .env). Existing users matched by email or username
are promoted to admin and their credentials updated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging  # noqa: E402
from app.db.init_db import (  # noqa: E402
    create_tables,
    ensure_admin,
    ensure_default_ips,
)
from app.db.session import SessionLocal  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update a WealthOS admin user."
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("ADMIN_EMAIL", ""),
        help="Admin email (default: ADMIN_EMAIL env variable)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("ADMIN_USERNAME", ""),
        help="Admin username (default: ADMIN_USERNAME env variable)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("ADMIN_PASSWORD", ""),
        help="Admin password (default: ADMIN_PASSWORD env variable)",
    )
    args = parser.parse_args(argv)
    missing = [
        flag
        for flag, value in (
            ("--email / ADMIN_EMAIL", args.email.strip()),
            ("--username / ADMIN_USERNAME", args.username.strip()),
            ("--password / ADMIN_PASSWORD", args.password.strip()),
        )
        if not value
    ]
    if missing:
        parser.error(f"missing required values: {', '.join(missing)}")
    return args


async def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = parse_args(argv)
    await create_tables()
    async with SessionLocal() as db:
        admin = await ensure_admin(
            db,
            email=args.email.strip(),
            username=args.username.strip(),
            password=args.password.strip(),
            overwrite=True,
        )
        await ensure_default_ips(db, admin.id)
        await db.commit()
        print(
            f"Admin ready: id={admin.id} email={admin.email} "
            f"username={admin.username}"
        )


if __name__ == "__main__":
    asyncio.run(main())
