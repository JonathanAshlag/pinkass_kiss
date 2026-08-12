"""Tests for create_admin — the seed_db.py alternative that provisions a single
admin user without any demo pages/workflow/other users.

background_repos() always resolves to the MongoDB mock in the test
environment (same reasoning as tests/scripts/test_seed_db.py).
"""

from app.models.user import PermissionLevel
from app.storage.mongo.users import MongoUserRepository
from scripts import create_admin


class TestCreateAdmin:
    async def test_creates_admin_user(self, mock_db):
        user = await create_admin.create_admin("admin1", "Admin")

        assert user.user_id == "admin1"
        assert user.name == "Admin"
        assert user.permission_level == PermissionLevel.admin

        stored = await MongoUserRepository(mock_db).get("admin1")
        assert stored is not None
        assert stored.permission_level == PermissionLevel.admin

    async def test_leaves_existing_user_untouched(self, mock_db):
        first = await create_admin.create_admin("admin1", "Admin")
        second = await create_admin.create_admin("admin1", "A Different Name")

        assert second.user_id == first.user_id
        assert second.name == first.name  # not overwritten

        user_repo = MongoUserRepository(mock_db)
        assert len(await user_repo.list_all()) == 1


class TestArgParsing:
    def test_reads_argv(self, monkeypatch):
        monkeypatch.setattr(create_admin.sys, "argv", ["create_admin.py", "admin1", "Admin"])
        assert create_admin._args() == ("admin1", "Admin")

    def test_reads_env_vars_when_no_argv(self, monkeypatch):
        monkeypatch.setattr(create_admin.sys, "argv", ["create_admin.py"])
        monkeypatch.setenv("ADMIN_USER_ID", "admin1")
        monkeypatch.setenv("ADMIN_NAME", "Admin")
        assert create_admin._args() == ("admin1", "Admin")

    def test_exits_when_no_args_or_env(self, monkeypatch):
        monkeypatch.setattr(create_admin.sys, "argv", ["create_admin.py"])
        monkeypatch.delenv("ADMIN_USER_ID", raising=False)
        monkeypatch.delenv("ADMIN_NAME", raising=False)

        try:
            create_admin._args()
            assert False, "expected SystemExit"
        except SystemExit:
            pass
