"""Create a single admin user — no demo pages, workflow, or other users.

Use this instead of seed_db.py when you need a real admin identity to log in
with but not the toy page hierarchy / demo workflow / other demo users (see
FIRST_EXPERIMENT_CHECKLIST.md). Safe to re-run: if user_id already exists,
it's reported and left untouched.

Usage:
  python scripts/create_admin.py <user_id> <name>
  # or via env vars:
  ADMIN_USER_ID=<user_id> ADMIN_NAME=<name> python scripts/create_admin.py

Respects DB_BACKEND env var (mongodb / postgres).
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.container import background_repos
from app.models.user import PermissionLevel, User


def _args() -> tuple[str, str]:
    if len(sys.argv) == 3:
        return sys.argv[1], sys.argv[2]
    if len(sys.argv) == 1:
        user_id = os.environ.get("ADMIN_USER_ID")
        name = os.environ.get("ADMIN_NAME")
        if user_id and name:
            return user_id, name
    sys.exit(
        "usage: python scripts/create_admin.py <user_id> <name>\n"
        "       (or set ADMIN_USER_ID and ADMIN_NAME env vars)"
    )


async def create_admin(user_id: str, name: str) -> User:
    """Create an admin user if user_id isn't already taken; return the stored user either way."""
    async with background_repos() as repos:
        existing = await repos.users.get(user_id)
        if existing:
            print(
                f"User {user_id!r} already exists "
                f"(permission_level={existing.permission_level.value}) — leaving it as is."
            )
            return existing

        user = User(user_id=user_id, name=name, permission_level=PermissionLevel.admin)
        await repos.users.create(user)
        print(f"✓ Created admin user {user_id!r} ({name})")
        return user


async def main() -> None:
    user_id, name = _args()

    if settings.db_backend == "postgres":
        from app.infrastructure.postgres.engine import init_engine
        init_engine(settings.postgres_uri)

    await create_admin(user_id, name)

    if settings.db_backend == "postgres":
        from app.infrastructure.postgres.engine import close_engine
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
