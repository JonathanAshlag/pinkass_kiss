"""Tests for seed_db — verifies the demo data is internally consistent and
still matches the current model/repository schema.

seed_db.py builds its USERS/WORKFLOW/PAGES fixtures by hand; nothing forces
them to stay valid as app/models/* evolves. These tests catch that drift:
static checks validate cross-references within the seed data itself, and the
live-run checks exercise seed() through the real repository layer.
"""

import pytest

from scripts import seed_db
from app.storage.mongo.pages import MongoPageRepository
from app.storage.mongo.users import MongoUserRepository
from app.storage.mongo.workflows import MongoWorkflowRepository


# ---------------------------------------------------------------------------
# Static consistency — cross-references within seed_db.py's own data
# ---------------------------------------------------------------------------

class TestSeedDataConsistency:
    def test_page_ids_are_unique(self):
        page_ids = [p.page_id for p in seed_db.PAGES]
        assert len(page_ids) == len(set(page_ids))

    def test_user_ids_are_unique(self):
        user_ids = [u.user_id for u in seed_db.USERS]
        assert len(user_ids) == len(set(user_ids))

    def test_all_parent_ids_reference_seeded_pages(self):
        page_ids = {p.page_id for p in seed_db.PAGES}
        for page in seed_db.PAGES:
            if page.parent_id is not None:
                assert page.parent_id in page_ids, f"{page.page_id} has unknown parent_id {page.parent_id!r}"

    def test_all_page_references_point_to_seeded_pages(self):
        page_ids = {p.page_id for p in seed_db.PAGES}
        for page in seed_db.PAGES:
            for ref in page.references:
                assert ref.page_id in page_ids, f"{page.page_id} references unknown page {ref.page_id!r}"

    def test_editor_workflow_ids_match_seeded_workflow(self):
        referenced = {u.workflow_id for u in seed_db.USERS if u.workflow_id is not None}
        assert referenced <= {seed_db.WORKFLOW.workflow_id}

    def test_workflow_steps_reference_seeded_users(self):
        user_ids = {u.user_id for u in seed_db.USERS}
        for step in seed_db.WORKFLOW.steps:
            assert step in user_ids, f"workflow step references unknown user {step!r}"

    def test_history_entries_reference_seeded_users(self):
        user_ids = {u.user_id for u in seed_db.USERS}
        for page in seed_db.PAGES:
            for entry in page.history:
                assert entry.user_id in user_ids
        for entry in seed_db.WORKFLOW.history:
            assert entry.user_id in user_ids

    def test_created_by_references_seeded_users(self):
        user_ids = {u.user_id for u in seed_db.USERS}
        for page in seed_db.PAGES:
            assert page.created_by in user_ids


# ---------------------------------------------------------------------------
# Live run — seed() actually persists through the repository layer.
#
# background_repos() always resolves to the MongoDB mock in the test
# environment (same reasoning as tests/integration/test_scheduler.py), so
# these run against the mongomock backend only.
# ---------------------------------------------------------------------------

class TestSeedRun:
    async def test_seed_creates_expected_counts(self, mock_db):
        await seed_db.seed()

        user_repo = MongoUserRepository(mock_db)
        wf_repo = MongoWorkflowRepository(mock_db)
        page_repo = MongoPageRepository(mock_db)

        assert len(await user_repo.list_all()) == len(seed_db.USERS)
        assert len(await wf_repo.list_all()) == 1
        for page in seed_db.PAGES:
            stored = await page_repo.get(page.page_id)
            assert stored is not None

    async def test_seeded_pages_match_source_data(self, mock_db):
        await seed_db.seed()
        page_repo = MongoPageRepository(mock_db)

        for page in seed_db.PAGES:
            stored = await page_repo.get(page.page_id)
            assert stored.title == page.title
            assert stored.parent_id == page.parent_id
            assert stored.status == page.status
            assert {r.page_id for r in stored.references} == {r.page_id for r in page.references}

    async def test_seeded_workflow_matches_source_data(self, mock_db):
        await seed_db.seed()
        wf_repo = MongoWorkflowRepository(mock_db)

        stored = await wf_repo.get(seed_db.WORKFLOW.workflow_id)
        assert stored is not None
        assert stored.name == seed_db.WORKFLOW.name
        assert stored.steps == seed_db.WORKFLOW.steps

    async def test_editor_with_workflow_points_to_a_real_workflow(self, mock_db):
        await seed_db.seed()
        user_repo = MongoUserRepository(mock_db)
        wf_repo = MongoWorkflowRepository(mock_db)

        for user in await user_repo.list_all():
            if user.workflow_id is not None:
                assert await wf_repo.get(user.workflow_id) is not None

    async def test_seed_is_idempotent(self, mock_db):
        """Re-running seed() (e.g. re-running the demo setup) clears first,
        so repeated runs don't accumulate duplicate/orphaned data."""
        await seed_db.seed()
        await seed_db.seed()

        user_repo = MongoUserRepository(mock_db)
        page_repo = MongoPageRepository(mock_db)

        assert len(await user_repo.list_all()) == len(seed_db.USERS)
        for page in seed_db.PAGES:
            assert await page_repo.get(page.page_id) is not None
