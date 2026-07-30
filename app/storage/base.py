from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.page import Page, HistoryEntry, Reference, ClassificationTriangle
    from app.models.user import User
    from app.models.workflow import Workflow
    from app.models.request import ApprovalRequest, RequestHistoryEntry


class PageRepository(ABC):
    @abstractmethod
    async def get(self, page_id: str) -> "Page | None": ...

    @abstractmethod
    async def create(self, page: "Page") -> None: ...

    @abstractmethod
    async def update_fields(self, page_id: str, fields: dict) -> None: ...

    @abstractmethod
    async def update_with_history(self, page_id: str, fields: dict, entry: "HistoryEntry") -> None: ...

    @abstractmethod
    async def append_history(self, page_id: str, entry: "HistoryEntry") -> None: ...

    @abstractmethod
    async def set_references(self, page_id: str, refs: "list[Reference]") -> None: ...

    @abstractmethod
    async def get_classification(self, page_id: str) -> "list[ClassificationTriangle] | None": ...

    @abstractmethod
    async def get_history(self, page_id: str) -> "list[HistoryEntry]": ...

    @abstractmethod
    async def search(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: "list[str] | None" = None,
    ) -> list[dict]: ...

    async def fuzzy_search(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: "list[str] | None" = None,
    ) -> list[dict]:
        return await self.search(query, statuses, limit, fields)

    @abstractmethod
    async def get_tree_nodes(self) -> list[dict]: ...

    @abstractmethod
    async def list_expired(self, today: str) -> list[dict]: ...

    @abstractmethod
    async def list_verified_published(self) -> list[dict]: ...

    @abstractmethod
    async def list_published_with_references(self) -> list[dict]: ...

    @abstractmethod
    async def update_inbound_link_count(self, page_id: str, count: int) -> None: ...

    @abstractmethod
    async def reset_inbound_link_counts(self, except_ids: list[str]) -> None: ...

    @abstractmethod
    async def delete(self, page_id: str) -> None: ...


class UserRepository(ABC):
    @abstractmethod
    async def get(self, user_id: str) -> "User | None": ...

    @abstractmethod
    async def create(self, user: "User") -> None: ...

    @abstractmethod
    async def list_all(self) -> "list[User]": ...

    @abstractmethod
    async def update_fields(self, user_id: str, fields: dict) -> None: ...

    @abstractmethod
    async def delete(self, user_id: str) -> bool: ...


class WorkflowRepository(ABC):
    @abstractmethod
    async def get(self, workflow_id: str) -> "Workflow | None": ...

    @abstractmethod
    async def create(self, wf: "Workflow") -> None: ...

    @abstractmethod
    async def list_all(self) -> "list[Workflow]": ...

    @abstractmethod
    async def update_fields(self, workflow_id: str, fields: dict) -> None: ...

    @abstractmethod
    async def update_with_history(self, workflow_id: str, fields: dict, entry: "HistoryEntry") -> None: ...

    @abstractmethod
    async def append_history(self, workflow_id: str, entry: "HistoryEntry") -> None: ...

    @abstractmethod
    async def delete_with_history(self, workflow_id: str, entry: "HistoryEntry") -> bool: ...


class RequestRepository(ABC):
    @abstractmethod
    async def get(self, request_id: str) -> "ApprovalRequest | None": ...

    @abstractmethod
    async def create(self, req: "ApprovalRequest") -> None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> "list[ApprovalRequest]": ...

    @abstractmethod
    async def list_all(self) -> "list[ApprovalRequest]": ...

    @abstractmethod
    async def update_fields(self, request_id: str, fields: dict) -> None: ...

    @abstractmethod
    async def update_with_history(self, request_id: str, fields: dict, entry: "RequestHistoryEntry") -> None: ...

    @abstractmethod
    async def get_pending_for_page(self, page_id: str, req_type: str) -> "ApprovalRequest | None": ...


class SourceFileRepository(ABC):
    @abstractmethod
    async def create(
        self,
        file_id: str,
        filename: str,
        content_type: str,
        uploaded_by: str,
        extracted_text: str,
    ) -> None: ...

    @abstractmethod
    async def set_page_ids(self, file_id: str, page_ids: list[str]) -> None: ...

    @abstractmethod
    async def get_for_page(self, page_id: str) -> "dict | None": ...

    @abstractmethod
    async def remove_page_id(self, file_id: str, page_id: str) -> "list[str]": ...

    @abstractmethod
    async def delete(self, file_id: str) -> None: ...
