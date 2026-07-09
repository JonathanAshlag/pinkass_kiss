"""Seed the database with sample data for demo purposes.

Creates:
- 4 users: admin, editor (with workflow), editor (without workflow), read-only
- 1 workflow: two-step approval
- Page hierarchy: Animals -> Dog, Cat, Sheep

Run: python seed_db.py
"""

import asyncio
import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "pinkas")

WORKFLOW_ID = "wf-demo-001"

USERS = [
    {
        "user_id": "admin1",
        "name": "מנהל מערכת",
        "permission_level": "admin",
        "workflow_id": None,
        "page_permissions": [],
    },
    {
        "user_id": "editor1",
        "name": "עורך עם תהליך",
        "permission_level": "editor",
        "workflow_id": WORKFLOW_ID,
        "page_permissions": ["animals"],
    },
    {
        "user_id": "editor2",
        "name": "עורך חופשי",
        "permission_level": "editor",
        "workflow_id": None,
        "page_permissions": ["animals", "plants"],
    },
    {
        "user_id": "reader1",
        "name": "קורא בלבד",
        "permission_level": "read_only",
        "workflow_id": None,
        "page_permissions": ["animals"],
    },
]

WORKFLOW = {
    "workflow_id": WORKFLOW_ID,
    "name": "תהליך אישור דו-שלבי",
    "description": "אישור ע\"י מנהל מערכת בשני שלבים",
    "steps": ["admin1", "admin1"],
    "history": [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": "admin1",
            "action": "create",
            "snapshot": "Created demo workflow",
            "comment": None,
            "diff": None,
        }
    ],
}

PAGES = [
    {
        "page_id": "animals",
        "title": "בעלי חיים",
        "parent_id": None,
        "content": "# בעלי חיים\n\nדף זה מכיל מידע על בעלי חיים שונים.\n\n## קטגוריות\n- יונקים\n- ציפורים\n- זוחלים",
        "references": [],
        "status": "published",
        "created_by": "admin1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "next_approval_date": None,
        "history": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": "admin1",
                "action": "create",
                "snapshot": "Initial creation",
                "comment": None,
                "diff": None,
            }
        ],
    },
    {
        "page_id": "dog",
        "title": "כלב",
        "parent_id": "animals",
        "content": "# כלב\n\nהכלב (Canis lupus familiaris) הוא יונק מבויית ממשפחת הכלביים.\n\n## מאפיינים\n- חיית מחמד נפוצה\n- נאמן לבעליו\n- קיימות מאות גזעים\n\n## שימושים\n- שמירה\n- ציד\n- הנחייה\n- חברה",
        "references": [{"type": "page", "page_id": "animals"}],
        "status": "published",
        "created_by": "admin1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "next_approval_date": None,
        "history": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": "admin1",
                "action": "create",
                "snapshot": "Initial creation",
                "comment": None,
                "diff": None,
            }
        ],
    },
    {
        "page_id": "cat",
        "title": "חתול",
        "parent_id": "animals",
        "content": "# חתול\n\nהחתול (Felis catus) הוא יונק טורף קטן מבויית.\n\n## מאפיינים\n- עצמאי\n- ציד מיומן\n- ראיית לילה מצוינת\n\n## התנהגות\n- ישן 12-16 שעות ביום\n- מטפח את עצמו\n- תקשורת באמצעות מיאו",
        "references": [{"type": "page", "page_id": "animals"}],
        "status": "published",
        "created_by": "admin1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "next_approval_date": None,
        "history": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": "admin1",
                "action": "create",
                "snapshot": "Initial creation",
                "comment": None,
                "diff": None,
            }
        ],
    },
    {
        "page_id": "sheep",
        "title": "כבשה",
        "parent_id": "animals",
        "content": "# כבשה\n\nהכבשה (Ovis aries) היא יונק מבויית ממשפחת הפריים.\n\n## מאפיינים\n- גידול צמר\n- חיה חברתית\n- עדרית\n\n## שימושים\n- צמר\n- חלב\n- בשר",
        "references": [{"type": "page", "page_id": "animals"}],
        "status": "published",
        "created_by": "admin1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "next_approval_date": None,
        "history": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": "admin1",
                "action": "create",
                "snapshot": "Initial creation",
                "comment": None,
                "diff": None,
            }
        ],
    },
    {
        "page_id": "plants",
        "title": "צמחים",
        "parent_id": None,
        "content": "# צמחים\n\nדף זה מכיל מידע על צמחים.",
        "references": [],
        "status": "published",
        "created_by": "admin1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "next_approval_date": None,
        "history": [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": "admin1",
                "action": "create",
                "snapshot": "Initial creation",
                "comment": None,
                "diff": None,
            }
        ],
    },
]


async def seed():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB]

    # Clear existing data
    await db.users.delete_many({})
    await db.workflows.delete_many({})
    await db.pages.delete_many({})
    await db.requests.delete_many({})

    # Insert data
    for user in USERS:
        await db.users.insert_one(user)
    print(f"✓ Created {len(USERS)} users")

    await db.workflows.insert_one(WORKFLOW)
    print(f"✓ Created workflow: {WORKFLOW['name']}")

    for page in PAGES:
        await db.pages.insert_one(page)
    print(f"✓ Created {len(PAGES)} pages")

    print("\n--- Demo Users ---")
    print(f"  admin1    - מנהל מערכת (admin)")
    print(f"  editor1   - עורך עם תהליך (editor + workflow)")
    print(f"  editor2   - עורך חופשי (editor, no workflow)")
    print(f"  reader1   - קורא בלבד (read_only)")
    print(f"\n--- Page Hierarchy ---")
    print(f"  בעלי חיים (animals)")
    print(f"    ├── כלב (dog)")
    print(f"    ├── חתול (cat)")
    print(f"    └── כבשה (sheep)")
    print(f"  צמחים (plants)")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
