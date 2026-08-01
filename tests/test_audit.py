"""Tests for audit logging — verifies logs are emitted with correct fields."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.audit import AuditAction, AuditLogEntry, AuditOutcome, UserContext
from app.models.page import PageCreate, PageUpdate
from app.models.user import User, PermissionLevel
from app.services.audit import emit_audit_log, _write_to_es
from app.services.mutations import apply_page_mutation
from app.models.request import RequestType


@pytest.fixture
def editor():
    return User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)


@pytest.fixture
def user_context():
    return UserContext(
        user_id="editor1",
        client_application_id="test-app",
        client_session_id="session-123",
    )


@pytest.fixture
def mock_es(monkeypatch):
    """Mock Elasticsearch client and enable audit logging."""
    from app.config import settings
    from app.infrastructure import elasticsearch as es_module

    monkeypatch.setattr(settings, "es_audit_enabled", True)

    mock_client = AsyncMock()
    mock_client.index = AsyncMock(return_value={"result": "created"})
    monkeypatch.setattr(es_module, "_es_client", mock_client)

    return mock_client


class TestAuditLogEntry:
    def test_user_context_minimal(self):
        ctx = UserContext(user_id="u1")
        assert ctx.user_id == "u1"
        assert ctx.client_application_id is None
        assert ctx.client_session_id is None

    def test_user_context_full(self):
        ctx = UserContext(
            user_id="u1",
            client_application_id="app1",
            client_session_id="sess1",
        )
        assert ctx.client_application_id == "app1"
        assert ctx.client_session_id == "sess1"

    def test_entry_has_timestamp(self, user_context):
        entry = AuditLogEntry(
            action=AuditAction.ask,
            user_context=user_context,
            outcome=AuditOutcome.success,
            request_path="/ask",
        )
        assert entry.timestamp is not None

    def test_entry_serializes_to_dict(self, user_context):
        entry = AuditLogEntry(
            action=AuditAction.create_page,
            user_context=user_context,
            resource_id="page-1",
            outcome=AuditOutcome.success,
            result={"page_id": "page-1", "status": "published"},
            latency_ms=42.5,
            request_path="/pages",
        )
        doc = entry.model_dump(mode="json")
        assert doc["action"] == "create_page"
        assert doc["user_context"]["user_id"] == "editor1"
        assert doc["user_context"]["client_application_id"] == "test-app"
        assert doc["resource_id"] == "page-1"
        assert doc["outcome"] == "success"
        assert doc["result"]["page_id"] == "page-1"
        assert doc["latency_ms"] == 42.5


class TestEmitAuditLog:
    def test_noop_when_disabled(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "es_audit_enabled", False)

        entry = AuditLogEntry(
            action=AuditAction.ask,
            user_context=UserContext(user_id="u1"),
            outcome=AuditOutcome.success,
            request_path="/ask",
        )
        emit_audit_log(entry)

    def test_noop_when_no_client(self, monkeypatch):
        from app.config import settings
        from app.infrastructure import elasticsearch as es_module

        monkeypatch.setattr(settings, "es_audit_enabled", True)
        monkeypatch.setattr(es_module, "_es_client", None)

        entry = AuditLogEntry(
            action=AuditAction.ask,
            user_context=UserContext(user_id="u1"),
            outcome=AuditOutcome.success,
            request_path="/ask",
        )
        emit_audit_log(entry)

    @pytest.mark.asyncio
    async def test_write_to_es_calls_index(self, mock_es, user_context):
        entry = AuditLogEntry(
            action=AuditAction.create_page,
            user_context=user_context,
            resource_id="page-1",
            outcome=AuditOutcome.success,
            result={"page_id": "page-1"},
            latency_ms=10.0,
            request_path="/pages",
        )
        await _write_to_es(entry)

        mock_es.index.assert_called_once()
        call_kwargs = mock_es.index.call_args[1]
        assert call_kwargs["index"].startswith("pinkas-audit-")
        doc = call_kwargs["document"]
        assert doc["action"] == "create_page"
        assert doc["user_context"]["user_id"] == "editor1"
        assert doc["user_context"]["client_application_id"] == "test-app"
        assert doc["outcome"] == "success"
        assert doc["resource_id"] == "page-1"
        assert doc["latency_ms"] == 10.0

    @pytest.mark.asyncio
    async def test_write_to_es_swallows_exceptions(self, mock_es, user_context):
        mock_es.index.side_effect = Exception("ES connection refused")

        entry = AuditLogEntry(
            action=AuditAction.ask,
            user_context=user_context,
            outcome=AuditOutcome.success,
            request_path="/ask",
        )
        await _write_to_es(entry)


class TestAuditIntegration:
    @pytest.mark.asyncio
    async def test_create_page_emits_audit_log(self, mock_es, editor, user_context, page_repo, req_repo):
        data = PageCreate(title="Test Page", description="desc", content="content")

        with patch("app.routers.pages.emit_audit_log") as mock_emit:
            from app.routers.pages import create_page_endpoint
            result = await create_page_endpoint(
                data=data, user=editor, user_context=user_context,
                page_repo=page_repo, req_repo=req_repo,
            )

            mock_emit.assert_called_once()
            entry = mock_emit.call_args[0][0]
            assert entry.action == AuditAction.create_page
            assert entry.outcome == AuditOutcome.success
            assert entry.user_context.user_id == "editor1"
            assert entry.user_context.client_application_id == "test-app"
            assert entry.resource_id == result.page["page_id"]
            assert entry.latency_ms > 0

    @pytest.mark.asyncio
    async def test_edit_page_emits_audit_log(self, mock_es, editor, user_context, page_repo, req_repo):
        from app.services.pages import create_page
        page = await create_page(
            PageCreate(title="Original", description="d", content="c"), editor, page_repo,
        )

        with patch("app.routers.pages.emit_audit_log") as mock_emit:
            from app.routers.pages import update_page_endpoint
            result = await update_page_endpoint(
                page_id=page.page_id, data=PageUpdate(content="updated"),
                user=editor, user_context=user_context,
                page_repo=page_repo, req_repo=req_repo,
            )

            mock_emit.assert_called_once()
            entry = mock_emit.call_args[0][0]
            assert entry.action == AuditAction.edit_page
            assert entry.outcome == AuditOutcome.success
            assert entry.resource_id == page.page_id
            assert entry.latency_ms > 0

    @pytest.mark.asyncio
    async def test_delete_page_emits_audit_log(self, mock_es, editor, user_context, page_repo, req_repo, source_file_repo):
        from app.services.pages import create_page
        page = await create_page(
            PageCreate(title="To Delete", description="d", content="c"), editor, page_repo,
        )

        with patch("app.routers.pages.emit_audit_log") as mock_emit:
            from app.routers.pages import delete_page_endpoint
            result = await delete_page_endpoint(
                page_id=page.page_id, user=editor, user_context=user_context,
                page_repo=page_repo, req_repo=req_repo, source_file_repo=source_file_repo,
            )

            mock_emit.assert_called_once()
            entry = mock_emit.call_args[0][0]
            assert entry.action == AuditAction.delete_page
            assert entry.outcome == AuditOutcome.success
            assert entry.resource_id == page.page_id

    @pytest.mark.asyncio
    async def test_edit_nonexistent_page_logs_denied(self, mock_es, editor, user_context, page_repo, req_repo):
        """Nonexistent page returns None from get_classification, so can_view_page
        returns False and we get a 403 (denied) rather than 404."""
        from fastapi import HTTPException

        with patch("app.routers.pages.emit_audit_log") as mock_emit:
            from app.routers.pages import update_page_endpoint
            with pytest.raises(HTTPException) as exc_info:
                await update_page_endpoint(
                    page_id="nonexistent", data=PageUpdate(content="x"),
                    user=editor, user_context=user_context,
                    page_repo=page_repo, req_repo=req_repo,
                )
            assert exc_info.value.status_code == 403

            mock_emit.assert_called_once()
            entry = mock_emit.call_args[0][0]
            assert entry.action == AuditAction.edit_page
            assert entry.outcome == AuditOutcome.denied
            assert entry.resource_id == "nonexistent"

    @pytest.mark.asyncio
    async def test_delete_classified_page_logs_denied(self, mock_es, user_context, page_repo, req_repo, source_file_repo):
        """A page with classification that the user cannot satisfy triggers denied."""
        from fastapi import HTTPException
        from app.services.pages import create_page

        editor = User(user_id="editor1", name="Editor", permission_level=PermissionLevel.editor)
        page = await create_page(
            PageCreate(
                title="Classified",
                description="d",
                content="c",
                classification=[{"id": "alpha", "level": 3}],
            ),
            editor,
            page_repo,
        )

        with patch("app.routers.pages.emit_audit_log") as mock_emit:
            from app.routers.pages import delete_page_endpoint
            with pytest.raises(HTTPException) as exc_info:
                await delete_page_endpoint(
                    page_id=page.page_id, user=editor, user_context=user_context,
                    page_repo=page_repo, req_repo=req_repo, source_file_repo=source_file_repo,
                )
            assert exc_info.value.status_code == 403

            mock_emit.assert_called_once()
            entry = mock_emit.call_args[0][0]
            assert entry.action == AuditAction.delete_page
            assert entry.outcome == AuditOutcome.denied
