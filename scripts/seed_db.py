"""
Populates the DB with a small tree of folders/files (plus one manifest) to play
around with the API against. Not idempotent -- re-running adds a second copy of
everything. For a clean slate: `alembic downgrade base && alembic upgrade head`.

Usage: `set -a && source .env && set +a && python scripts/seed_db.py`
"""

from datetime import datetime, timedelta, timezone

from kb import service
from kb.storage.db import SessionLocal


def main() -> None:
    with SessionLocal() as session:
        engineering = service.create_folder(
            session,
            parent_id=None,
            kind="skeleton",
            title="Engineering",
            description="Everything for the eng team.",
            tags=["eng"],
        )
        runbooks = service.create_folder(
            session,
            parent_id=engineering.id,
            title="Runbooks",
            agent_locked=True,
            tags=["eng", "ops"],
        )
        product = service.create_folder(
            session,
            parent_id=None,
            title="Product",
            description="Product docs and planning.",
            tags=["product"],
        )

        onboarding = service.create_file(
            session,
            parent_id=engineering.id,
            title="Onboarding",
            description="How to get set up as a new engineer.",
            tags=["eng", "onboarding"],
            status="stable",
            content="# Onboarding\n\nWelcome! Start by reading the [Deploy runbook]"
            f"(db://files/{runbooks.id}).",
            sources=[{"id": "handbook", "resource": "okf://internal/handbook"}],
        )

        deploy = service.create_file(
            session,
            parent_id=runbooks.id,
            title="Deploy",
            description="How to ship to prod.",
            tags=["eng", "ops"],
            status="stable",
            # Deliberately broken internal link + a stale_after in the past, so
            # GET /nodes/{id} shows both kinds of advisory warnings.
            content="# Deploy\n\nSee [old CI doc](db://files/00000000-0000-0000-0000-000000000000).",
            stale_after=datetime.now(timezone.utc) - timedelta(days=1),
        )

        roadmap = service.create_file(
            session,
            parent_id=product.id,
            title="Roadmap",
            description="Q4 priorities.",
            tags=["product", "planning"],
            status="draft",
            # Deliberately missing sources[].resource + an unresolved footnote,
            # so GET /nodes/{id} shows the frontmatter-validation warnings too.
            content="# Roadmap\n\nBiggest bet this quarter[^bet].",
            sources=[{"id": "bet"}],
        )

        manifest = service.create_manifest(
            session, "demo-agent", "Everything a demo agent should see."
        )
        service.add_manifest_member(session, manifest.id, node_id=engineering.id)
        service.add_manifest_member(session, manifest.id, node_id=roadmap.id)

        session.commit()

        print("Seeded:")
        print(f"  Engineering  {engineering.id}  (skeleton folder: can't rename/move/delete)")
        print(f"    Runbooks   {runbooks.id}  (folder, locked for agents)")
        print(f"      Deploy   {deploy.id}  (file, stale + broken link warnings)")
        print(f"    Onboarding {onboarding.id}  (file, clean)")
        print(f"  Product      {product.id}  (folder)")
        print(f"    Roadmap    {roadmap.id}  (file, missing-resource + unresolved-footnote warnings)")
        print(f"  demo-agent   {manifest.id}  (manifest: Engineering subtree + Roadmap)")


if __name__ == "__main__":
    main()
