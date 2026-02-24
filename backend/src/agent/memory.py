"""
Conversation memory for the Double Edge AI Agent.

Provides CRUD operations for agent conversations and messages,
plus pending action storage for the confirmation gate.

All storage uses PostgreSQL (agent_conversations, agent_messages,
agent_pending_actions tables defined in src/models/legacy.py).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from src.agent.tools import _pg_conn

logger = logging.getLogger(__name__)


def create_conversation(title: str = "New conversation") -> str:
    """Create a new conversation. Returns conversation_id."""
    from sqlalchemy import text
    conversation_id = str(uuid4())
    now = datetime.now(timezone.utc)
    with _pg_conn() as conn:
        conn.execute(
            text("""INSERT INTO agent_conversations
                    (conversation_id, title, created_at, updated_at)
                    VALUES (:cid, :title, :now, :now)"""),
            {"cid": conversation_id, "title": title, "now": now},
        )
        conn.commit()
    logger.info(f"Created conversation {conversation_id}: {title}")
    return conversation_id


def update_conversation_title(conversation_id: str, title: str) -> None:
    """Update the title of a conversation."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        conn.execute(
            text("UPDATE agent_conversations SET title = :title, updated_at = :now WHERE conversation_id = :cid"),
            {"title": title, "now": datetime.now(timezone.utc), "cid": conversation_id},
        )
        conn.commit()


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    structured_data: Optional[dict] = None,
    claude_messages: Optional[list] = None,
) -> str:
    """Add a message to a conversation. Returns message_id."""
    from sqlalchemy import text
    message_id = str(uuid4())
    now = datetime.now(timezone.utc)
    with _pg_conn() as conn:
        conn.execute(
            text("""INSERT INTO agent_messages
                    (message_id, conversation_id, role, content, structured_data, claude_messages, created_at, updated_at)
                    VALUES (:mid, :cid, :role, :content, :sd, :cm, :now, :now)"""),
            {
                "mid": message_id,
                "cid": conversation_id,
                "role": role,
                "content": content,
                "sd": json.dumps(structured_data) if structured_data else None,
                "cm": json.dumps(claude_messages) if claude_messages else None,
                "now": now,
            },
        )
        conn.execute(
            text("UPDATE agent_conversations SET updated_at = :now WHERE conversation_id = :cid"),
            {"now": now, "cid": conversation_id},
        )
        conn.commit()
    return message_id


def get_messages(conversation_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation, ordered by created_at ascending."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        rows = conn.execute(
            text("""SELECT message_id, conversation_id, role, content, structured_data, claude_messages, created_at
                    FROM agent_messages
                    WHERE conversation_id = :cid
                    ORDER BY created_at ASC
                    LIMIT :limit"""),
            {"cid": conversation_id, "limit": limit},
        ).fetchall()

    messages = []
    for row in rows:
        msg = dict(row._mapping)
        for field in ("structured_data", "claude_messages"):
            if msg.get(field) and isinstance(msg[field], str):
                try:
                    msg[field] = json.loads(msg[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        # Normalize datetime to ISO string for JSON serialisation
        if msg.get("created_at") and hasattr(msg["created_at"], "isoformat"):
            msg["created_at"] = msg["created_at"].isoformat()
        messages.append(msg)
    return messages


def get_message_count(conversation_id: str) -> int:
    """Get the number of messages in a conversation."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM agent_messages WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        ).fetchone()
    return row[0] if row else 0


def list_conversations(limit: int = 20) -> list[dict]:
    """List recent conversations with message counts."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        rows = conn.execute(
            text("""SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                           COUNT(m.message_id) AS message_count
                    FROM agent_conversations c
                    LEFT JOIN agent_messages m ON c.conversation_id = m.conversation_id
                    GROUP BY c.conversation_id, c.title, c.created_at, c.updated_at
                    ORDER BY c.updated_at DESC
                    LIMIT :limit"""),
            {"limit": limit},
        ).fetchall()

    result = []
    for row in rows:
        r = dict(row._mapping)
        for f in ("created_at", "updated_at"):
            if r.get(f) and hasattr(r[f], "isoformat"):
                r[f] = r[f].isoformat()
        result.append(r)
    return result


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and all its messages."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        conn.execute(
            text("DELETE FROM agent_pending_actions WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
        conn.execute(
            text("DELETE FROM agent_messages WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
        result = conn.execute(
            text("DELETE FROM agent_conversations WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
        conn.commit()
    return result.rowcount > 0


def conversation_exists(conversation_id: str) -> bool:
    """Check if a conversation exists."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        row = conn.execute(
            text("SELECT 1 FROM agent_conversations WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Pending Actions (for confirmation gate)
# ---------------------------------------------------------------------------

def save_pending_action(
    conversation_id: str,
    action_id: str,
    tool_name: str,
    tool_input: dict,
    description: str,
    tool_use_id: Optional[str] = None,
) -> None:
    """Save a pending action that requires user confirmation."""
    from sqlalchemy import text
    stored_input = dict(tool_input)
    if tool_use_id:
        stored_input["_tool_use_id"] = tool_use_id
    now = datetime.now(timezone.utc)

    with _pg_conn() as conn:
        conn.execute(
            text("DELETE FROM agent_pending_actions WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
        conn.execute(
            text("""INSERT INTO agent_pending_actions
                    (action_id, conversation_id, tool_name, tool_input, description, created_at, updated_at)
                    VALUES (:aid, :cid, :tool, :input, :desc, :now, :now)"""),
            {
                "aid": action_id,
                "cid": conversation_id,
                "tool": tool_name,
                "input": json.dumps(stored_input),
                "desc": description,
                "now": now,
            },
        )
        conn.commit()
    logger.info(f"Saved pending action {action_id} for {conversation_id}: {tool_name}")


def get_pending_action(conversation_id: str, action_id: str) -> Optional[dict]:
    """Get a pending action by conversation and action ID."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        row = conn.execute(
            text("""SELECT action_id, conversation_id, tool_name, tool_input, description, created_at
                    FROM agent_pending_actions
                    WHERE conversation_id = :cid AND action_id = :aid"""),
            {"cid": conversation_id, "aid": action_id},
        ).fetchone()
    if not row:
        return None
    result = dict(row._mapping)
    try:
        result["tool_input"] = json.loads(result["tool_input"])
    except (json.JSONDecodeError, TypeError):
        pass
    return result


def clear_pending_actions(conversation_id: str) -> None:
    """Clear all pending actions for a conversation."""
    from sqlalchemy import text
    with _pg_conn() as conn:
        conn.execute(
            text("DELETE FROM agent_pending_actions WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        )
        conn.commit()
