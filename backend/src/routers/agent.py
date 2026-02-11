"""
Agent router — AI agent chat, conversations, and briefings.
Extracted from the monolithic api.py for maintainability.
"""
import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.agent.types import AgentChatRequest
from src.agent.orchestrator import run_agent
from src.agent import memory as agent_memory
from src.agent.email_service import get_briefing, send_briefing_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/chat")
@limiter.limit("20/minute")
async def agent_chat(request_obj: Request, request: AgentChatRequest):
    """SSE streaming endpoint for agent chat."""
    has_msg = bool(request.message and request.message.strip())
    has_conf = request.confirmation is not None
    if not has_msg and not has_conf:
        raise HTTPException(400, "Provide either a message or a confirmation")
    if has_msg and has_conf:
        raise HTTPException(400, "Provide either a message or a confirmation, not both")

    async def event_generator():
        async for event in run_agent(
            message=request.message,
            conversation_id=request.conversation_id,
            confirmation=request.confirmation,
        ):
            yield event

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/conversations")
async def list_agent_conversations():
    """Returns list of past conversations."""
    conversations = agent_memory.list_conversations(limit=20)
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}")
async def get_agent_conversation(conversation_id: str):
    """Returns full message history for a conversation."""
    if not agent_memory.conversation_exists(conversation_id):
        raise HTTPException(404, "Conversation not found")
    messages = agent_memory.get_messages(conversation_id)
    return {"conversation_id": conversation_id, "messages": messages}


@router.delete("/conversations/{conversation_id}")
async def delete_agent_conversation(conversation_id: str):
    """Delete a conversation and all its messages."""
    deleted = agent_memory.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(404, "Conversation not found")
    return {"status": "deleted"}


@router.get("/briefing/{briefing_id}")
async def get_agent_briefing(briefing_id: str):
    """Serve a saved HTML briefing for download."""
    html = get_briefing(briefing_id)
    if not html:
        raise HTTPException(404, "Briefing not found")
    return HTMLResponse(content=html)


@router.post("/briefing/{briefing_id}/send")
async def send_agent_briefing(briefing_id: str, recipient: Optional[str] = Query(None)):
    """Send a saved briefing via email."""
    html = get_briefing(briefing_id)
    if not html:
        raise HTTPException(404, "Briefing not found")
    to = recipient or os.environ.get("EMAIL_TO_DEFAULT", "")
    if not to:
        raise HTTPException(400, "No recipient email provided and no default configured")
    result = send_briefing_email("HPD Leads Briefing", html, to)
    return result
