---
paths:
  - app/storage/**/*.py
  - app/container.py
---

# Storage layer: Repository pattern

All storage access is abstracted through repositories (`PageRepository`, `UserRepository`, etc.) defined in `app/storage/base.py`. The composition root (`app/container.py`) centralizes backend selection (MongoDB vs. Postgres) and provides type-aliased dependencies to routers.

**When working with storage:**

- Import repository types from `app.container` (e.g., `from app.container import PageRepo`), never import concrete classes directly from `app/storage/mongo/` or `app/storage/postgres/`.
- When adding a new method to a repository: implement it in **both** `app/storage/mongo/` and `app/storage/postgres/` versions. Partial implementations cause runtime failures only on the unused backend.
- MongoDB and Postgres implementations may diverge in query semantics (filtering, sorting, pagination). If behavior differs between backends, this is usually a bug — test against both or document the intentional difference in the base class docstring.
- GridFS file storage is always in MongoDB, even when structured data is in Postgres. Don't assume PostgreSQL has files.
