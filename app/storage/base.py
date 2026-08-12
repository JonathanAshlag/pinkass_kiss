from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.page import Page, HistoryEntry, Reference, ClassificationTriangle
    from app.models.user import User
    from app.models.workflow import Workflow
    from app.models.request import ApprovalRequest, RequestHistoryEntry
    from app.models.agent import Agent
    from app.models.bundle import Bundle


# Fields that downstream code (permission filtering in app/services/permissions.py,
# trust-tier sorting in app/services/pages.py) always needs present on a page
# search/list result, regardless of which fields the caller explicitly requested.
# Lives here — not in app/services/ — so both the service layer and every storage
# adapter (Mongo, Postgres) can share one definition without adapters depending on
# the service layer.
ALWAYS_INCLUDED_FIELDS: frozenset[str] = frozenset(
    {"classification", "trust_tier", "inbound_link_count", "page_id"}
)


def with_always_included_fields(fields: "list[str] | None") -> "set[str] | None":
    """Merge caller-requested fields with ALWAYS_INCLUDED_FIELDS.

    `None` is passed through unchanged since it conventionally means "all fields".
    """
    if fields is None:
        return None
    return set(fields) | ALWAYS_INCLUDED_FIELDS


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
    async def search_by_name(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: "list[str] | None" = None,
        tags: "list[str] | None" = None,
    ) -> list[dict]:
        """Match pages by title or alias only (not content/description)."""
        ...

    async def fuzzy_search_by_name(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: "list[str] | None" = None,
        tags: "list[str] | None" = None,
    ) -> list[dict]:
        """Fuzzy-match pages by title or alias only (not content/description)."""
        return await self.search_by_name(query, statuses, limit, fields, tags=tags)

    async def find_similar_for_dedup(
        self,
        title: str,
        description: str,
        threshold: float,
        limit: int,
        fields: "list[str] | None" = None,
    ) -> list[dict]:
        from app.models.page import PageStatus
        statuses = [s.value for s in PageStatus if s != PageStatus.deleted]
        return await self.search_by_name(f"{title} {description}", statuses, limit, fields)

    async def fuzzy_search_scored(
        self,
        query: str,
        statuses: list[str],
        limit: int,
        fields: "list[str] | None" = None,
        tags: "list[str] | None" = None,
    ) -> list[dict]:
        """Fuzzy-match pages by title or alias, with a numeric 'score' field per result.
        Default: rank-based synthetic score. Postgres overrides with real word_similarity."""
        docs = await self.fuzzy_search_by_name(query, statuses, limit, fields, tags=tags)
        for i, d in enumerate(docs):
            d["score"] = max(0.0, 1.0 - i * 0.05)
        return docs

    async def list_scannable_pages(
        self,
        fields: "list[str] | None" = None,
    ) -> list[dict]:
        """Return all published pages for passive-scan caching.
        Must return complete list, not limited."""
        from app.models.page import PageStatus
        return await self.search_by_name("", [PageStatus.published.value], limit=100_000, fields=fields)

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


class AgentRepository(ABC):
    @abstractmethod
    async def get(self, agent_id: str) -> "Agent | None": ...

    @abstractmethod
    async def get_by_api_key_hash(self, api_key_hash: str) -> "Agent | None": ...

    @abstractmethod
    async def create(self, agent: "Agent") -> None: ...

    @abstractmethod
    async def list_all(self) -> "list[Agent]": ...

    @abstractmethod
    async def update_fields(self, agent_id: str, fields: dict) -> None: ...


class BundleRepository(ABC):
    @abstractmethod
    async def get(self, name: str) -> "Bundle | None": ...

    @abstractmethod
    async def upsert(self, bundle: "Bundle") -> None: ...

    @abstractmethod
    async def list_all(self) -> "list[Bundle]": ...
