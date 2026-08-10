"""Agent authentication dependencies."""

import uuid

from fastapi import Header, HTTPException, Request

from app.container import AgentRepo, UserRepo
from app.models.agent import Agent, AgentRequestContext
from app.models.audit import AuditAction, AuditLogEntry, AuditOutcome, UserContext
from app.models.user import User
from app.services.agent_provisioning import _hash_api_key
from app.services.event_log import emit_audit_log


def _get_or_create_request_id(request: Request) -> str:
    """One id per incoming HTTP request, shared by every log entry it produces
    (even across dependencies), so a request's path through the system can be
    reconstructed by filtering logs on this value."""
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = str(uuid.uuid4())
        request.state.request_id = rid
    return rid


async def get_current_agent(
    request: Request,
    x_api_key: str = Header(...),
    agent_repo: AgentRepo = None,
    user_repo: UserRepo = None,
) -> tuple[Agent, User]:
    """Authenticate an agent by API key and resolve its linked user."""
    request_id = _get_or_create_request_id(request)
    key_hash = _hash_api_key(x_api_key)
    agent = await agent_repo.get_by_api_key_hash(key_hash)
    if not agent or not agent.active:
        emit_audit_log(AuditLogEntry(
            request_id=request_id,
            action=AuditAction.agent_auth,
            user_context=UserContext(user_id=f"unresolved:{key_hash}"),
            outcome=AuditOutcome.denied,
            result={"reason": "invalid_or_inactive_api_key"},
            request_path=request.url.path,
        ))
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    user = await user_repo.get(agent.user_id)
    if not user:
        emit_audit_log(AuditLogEntry(
            request_id=request_id,
            action=AuditAction.agent_auth,
            user_context=UserContext(user_id=agent.user_id, client_application_id=agent.agent_id),
            outcome=AuditOutcome.denied,
            result={"reason": "linked_user_not_found"},
            request_path=request.url.path,
        ))
        raise HTTPException(status_code=401, detail="Agent's linked user not found")

    return agent, user


async def get_agent_request_context(
    request: Request,
    x_api_key: str = Header(...),
    x_session_id: str = Header(...),
    agent_repo: AgentRepo = None,
    user_repo: UserRepo = None,
) -> AgentRequestContext:
    """Assemble the full context for an agent API request (auth + correlation ids)."""
    agent, user = await get_current_agent(request, x_api_key, agent_repo, user_repo)
    return AgentRequestContext(
        agent=agent,
        user=user,
        session_id=x_session_id,
        request_id=_get_or_create_request_id(request),
    )
