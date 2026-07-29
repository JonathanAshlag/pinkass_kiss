"""Tests for init_db — verifies MongoDB index creation and Postgres migration wiring."""

import pytest
from unittest.mock import MagicMock, patch

from mongomock_motor import AsyncMongoMockClient

import init_db


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------

class TestInitMongo:
    @pytest.fixture
    def mongo_client(self):
        client = AsyncMongoMockClient()
        yield client
        client.close()

    @pytest.fixture(autouse=True)
    def _patch_settings(self, monkeypatch):
        monkeypatch.setattr(init_db.settings, "mongo_uri", "mongodb://localhost:27017")
        monkeypatch.setattr(init_db.settings, "mongo_db", "test_pinkas")

    async def _run(self, mongo_client):
        with patch("motor.motor_asyncio.AsyncIOMotorClient", return_value=mongo_client):
            await init_db.init_mongo()

    async def test_pages_indexes_created(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        index_names = set((await db.pages.index_information()).keys())
        assert {
            "pages_text_search",
            "pages_parent_id",
            "pages_page_id",
            "pages_approval_date",
            "pages_status",
            "pages_trust_tier",
            "pages_trust_link_rank",
        } <= index_names

    async def test_page_id_index_is_unique(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        info = await db.pages.index_information()
        assert info["pages_page_id"].get("unique") is True

    async def test_users_index_created(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        index_names = set((await db.users.index_information()).keys())
        assert "users_user_id" in index_names

    async def test_user_id_index_is_unique(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        info = await db.users.index_information()
        assert info["users_user_id"].get("unique") is True

    async def test_workflows_index_created(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        index_names = set((await db.workflows.index_information()).keys())
        assert "workflows_workflow_id" in index_names

    async def test_requests_indexes_created(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        index_names = set((await db.requests.index_information()).keys())
        assert {
            "requests_request_id",
            "requests_status",
            "requests_requested_by",
        } <= index_names

    async def test_source_files_index_created(self, mongo_client):
        await self._run(mongo_client)
        db = mongo_client["test_pinkas"]
        index_names = set((await db.source_files.index_information()).keys())
        assert "source_files_file_id" in index_names


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------

class TestInitPostgres:
    @pytest.fixture(autouse=True)
    def _patch_settings(self, monkeypatch):
        monkeypatch.setattr(
            init_db.settings, "postgres_uri",
            "postgresql+asyncpg://localhost/pinkas_test",
        )

    def test_calls_alembic_upgrade_to_head(self):
        mock_cfg = MagicMock()
        captured = {}

        def fake_upgrade(cfg, target):
            captured["cfg"] = cfg
            captured["target"] = target

        with patch("alembic.config.Config", return_value=mock_cfg), \
             patch("alembic.command.upgrade", side_effect=fake_upgrade):
            init_db.init_postgres()

        assert captured["target"] == "head"
        assert captured["cfg"] is mock_cfg

    def test_postgres_uri_passed_to_alembic(self):
        mock_cfg = MagicMock()

        with patch("alembic.config.Config", return_value=mock_cfg), \
             patch("alembic.command.upgrade"):
            init_db.init_postgres()

        mock_cfg.set_main_option.assert_called_once_with(
            "sqlalchemy.url", "postgresql+asyncpg://localhost/pinkas_test"
        )

    def test_alembic_ini_is_loaded(self):
        mock_cfg = MagicMock()

        with patch("alembic.config.Config", return_value=mock_cfg) as MockConfig, \
             patch("alembic.command.upgrade"):
            init_db.init_postgres()

        MockConfig.assert_called_once_with("alembic.ini")


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------

class TestMain:
    async def test_dispatches_to_postgres(self, monkeypatch):
        monkeypatch.setattr(init_db.settings, "db_backend", "postgres")
        called = {}

        def fake_postgres():
            called["postgres"] = True

        monkeypatch.setattr(init_db, "init_postgres", fake_postgres)
        await init_db.main()

        assert called.get("postgres") is True

    async def test_dispatches_to_mongo(self, monkeypatch):
        monkeypatch.setattr(init_db.settings, "db_backend", "mongodb")
        called = {}

        async def fake_mongo():
            called["mongo"] = True

        monkeypatch.setattr(init_db, "init_mongo", fake_mongo)
        await init_db.main()

        assert called.get("mongo") is True

    async def test_postgres_does_not_call_mongo(self, monkeypatch):
        monkeypatch.setattr(init_db.settings, "db_backend", "postgres")
        called = {}

        monkeypatch.setattr(init_db, "init_postgres", lambda: None)

        async def fake_mongo():
            called["mongo"] = True

        monkeypatch.setattr(init_db, "init_mongo", fake_mongo)
        await init_db.main()

        assert "mongo" not in called

    async def test_mongo_does_not_call_postgres(self, monkeypatch):
        monkeypatch.setattr(init_db.settings, "db_backend", "mongodb")
        called = {}

        def fake_postgres():
            called["postgres"] = True

        monkeypatch.setattr(init_db, "init_postgres", fake_postgres)

        async def fake_mongo():
            pass

        monkeypatch.setattr(init_db, "init_mongo", fake_mongo)
        await init_db.main()

        assert "postgres" not in called
