"""Seed the database with sample data for demo purposes.

Creates:
- 4 users: admin, editor (with workflow), editor (without workflow), read-only
- 1 workflow: two-step approval
- Page hierarchy: Animals -> Dog, Cat, Sheep, Plants

Run: python seed_db.py
Respects DB_BACKEND env var (mongodb / postgres).
"""

import asyncio
from datetime import datetime, timezone

from app.config import settings
from app.container import background_repos
from app.models.page import HistoryEntry, Page, PageStatus, Reference, ReferenceType
from app.models.user import PermissionLevel, User
from app.models.workflow import Workflow

WORKFLOW_ID = "wf-demo-001"

_NOW = datetime.now(timezone.utc)


def _init_history(action: str = "create", snapshot: str = "Initial creation") -> list[HistoryEntry]:
    return [HistoryEntry(timestamp=_NOW, user_id="admin1", action=action, snapshot=snapshot)]


USERS = [
    User(user_id="admin1", name="מנהל מערכת", permission_level=PermissionLevel.admin),
    User(user_id="editor1", name="עורך עם תהליך", permission_level=PermissionLevel.editor, workflow_id=WORKFLOW_ID),
    User(user_id="editor2", name="עורך חופשי", permission_level=PermissionLevel.editor),
    User(user_id="reader1", name="קורא בלבד", permission_level=PermissionLevel.read_only),
]

WORKFLOW = Workflow(
    workflow_id=WORKFLOW_ID,
    name="תהליך אישור דו-שלבי",
    description='אישור ע"י מנהל מערכת בשני שלבים',
    steps=["admin1", "admin1"],
    history=_init_history(action="create", snapshot="Created demo workflow"),
)

PAGES = [
    Page(
        page_id="animals",
        title="בעלי חיים",
        description="מידע על בעלי חיים שונים",
        content="# בעלי חיים\n\nדף זה מכיל מידע על בעלי חיים שונים.\n\n## קטגוריות\n- יונקים\n- ציפורים\n- זוחלים",
        status=PageStatus.published,
        created_by="admin1",
        created_at=_NOW,
        updated_at=_NOW,
        history=_init_history(),
    ),
    Page(
        page_id="dog",
        title="כלב",
        description="כלב — Canis lupus familiaris",
        parent_id="animals",
        content="# כלב\n\nהכלב (Canis lupus familiaris) הוא יונק מבויית ממשפחת הכלביים.\n\n## מאפיינים\n- חיית מחמד נפוצה\n- נאמן לבעליו\n- קיימות מאות גזעים\n\n## שימושים\n- שמירה\n- ציד\n- הנחייה\n- חברה",
        references=[Reference(type=ReferenceType.page, page_id="animals")],
        status=PageStatus.published,
        created_by="admin1",
        created_at=_NOW,
        updated_at=_NOW,
        history=_init_history(),
    ),
    Page(
        page_id="cat",
        title="חתול",
        description="חתול — Felis catus",
        parent_id="animals",
        content="# חתול\n\nהחתול (Felis catus) הוא יונק טורף קטן מבויית.\n\n## מאפיינים\n- עצמאי\n- ציד מיומן\n- ראיית לילה מצוינת\n\n## התנהגות\n- ישן 12-16 שעות ביום\n- מטפח את עצמו\n- תקשורת באמצעות מיאו",
        references=[Reference(type=ReferenceType.page, page_id="animals")],
        status=PageStatus.published,
        created_by="admin1",
        created_at=_NOW,
        updated_at=_NOW,
        history=_init_history(),
    ),
    Page(
        page_id="sheep",
        title="כבשה",
        description="כבשה — Ovis aries",
        parent_id="animals",
        content="# כבשה\n\nהכבשה (Ovis aries) היא יונק מבויית ממשפחת הפריים.\n\n## מאפיינים\n- גידול צמר\n- חיה חברתית\n- עדרית\n\n## שימושים\n- צמר\n- חלב\n- בשר",
        references=[Reference(type=ReferenceType.page, page_id="animals")],
        status=PageStatus.published,
        created_by="admin1",
        created_at=_NOW,
        updated_at=_NOW,
        history=_init_history(),
    ),
    Page(
        page_id="plants",
        title="צמחים",
        description="מידע על צמחים",
        content="# צמחים\n\nדף זה מכיל מידע על צמחים.",
        status=PageStatus.published,
        created_by="admin1",
        created_at=_NOW,
        updated_at=_NOW,
        history=_init_history(),
    ),
]


async def _clear_mongo() -> None:
    from app.infrastructure.mongo import get_db
    db = get_db()
    await db.users.delete_many({})
    await db.workflows.delete_many({})
    await db.pages.delete_many({})
    await db.requests.delete_many({})


async def _clear_postgres() -> None:
    from sqlalchemy import text
    from app.infrastructure.postgres.engine import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        # Order matters: refs/revisions before pages, requests before nothing
        for table in ("page_refs", "page_revisions", "requests", "pages", "workflows", "users"):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()


async def seed() -> None:
    # Initialize backends
    from app.infrastructure.mongo import init_client
    init_client()
    if settings.db_backend == "postgres":
        from app.infrastructure.postgres.engine import init_engine
        init_engine(settings.postgres_uri)

    # Clear existing data
    if settings.db_backend == "postgres":
        await _clear_postgres()
    else:
        await _clear_mongo()

    # Insert via repository layer (backend-agnostic)
    async with background_repos() as repos:
        for user in USERS:
            await repos.users.create(user)
        print(f"✓ Created {len(USERS)} users")

        await repos.workflows.create(WORKFLOW)
        print(f"✓ Created workflow: {WORKFLOW.name}")

        for page in PAGES:
            await repos.pages.create(page)
        print(f"✓ Created {len(PAGES)} pages")

    print(f"\n--- Backend: {settings.db_backend} ---")
    print("\n--- Demo Users ---")
    print("  admin1    - מנהל מערכת (admin)")
    print("  editor1   - עורך עם תהליך (editor + workflow)")
    print("  editor2   - עורך חופשי (editor, no workflow)")
    print("  reader1   - קורא בלבד (read_only)")
    print("\n--- Page Hierarchy ---")
    print("  בעלי חיים (animals)")
    print("    ├── כלב (dog)")
    print("    ├── חתול (cat)")
    print("    └── כבשה (sheep)")
    print("  צמחים (plants)")

    if settings.db_backend == "postgres":
        from app.infrastructure.postgres.engine import close_engine
        await close_engine()


if __name__ == "__main__":
    asyncio.run(seed())
