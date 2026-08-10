---
paths:
  - app/infrastructure/postgres/migrations/**
  - app/infrastructure/postgres/models.py
  - init_db.py
---

# Postgres migrations

The app is pre-launch: no production data to preserve. The project maintains a single migration file (`001_initial_schema.py`) instead of an accumulating chain.

**Modifying schema:** Prefer amending `001_initial_schema.py` directly. Always ask permission first — editing `001` retroactively changes "already applied" for any env stamped at that revision.

**Resetting after a squashed migration:** If your local Postgres DB was migrated to a revision that no longer exists (Alembic error: `Can't locate revision identified by '...'`), reset and re-migrate:

```bash
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings

async def main():
    engine = create_async_engine(settings.postgres_uri)
    async with engine.begin() as conn:
        await conn.exec_driver_sql('DROP SCHEMA IF EXISTS pinkass CASCADE')
    await engine.dispose()

asyncio.run(main())
"
python init_db.py
```

This is destructive — only when Postgres data is disposable.
