"""
Conversation memory for the HPD Leads AI Agent.

Provides CRUD operations for agent conversations and messages,
plus pending action storage for the confirmation gate.
"""

import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from src.storage.database import get_database

logger = logging.getLogger(__name__)


def create_conversation(title: str = "New conversation") -> str:
    """Create a new conversation. Returns conversation_id."""
    db = get_database()
    conversation_id = str(uuid4())
    now = datetime.now().isoformat()
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO agent_conversations (conversation_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, title, now, now),
        )
        conn.commit()
    logger.info(f"Created conversation {conversation_id}: {title}")
    return conversation_id


def update_conversation_title(conversation_id: str, title: str) -> None:
    """Update the title of a conversation."""
    db = get_database()
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE agent_conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
            (title, datetime.now().isoformat(), conversation_id),
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
    db = get_database()
    message_id = str(uuid4())
    now = datetime.now().isoformat()
    with db._get_connection() as conn:
        conn.execute(
            """INSERT INTO agent_messages
               (message_id, conversation_id, role, content, structured_data, claude_messages, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id,
                conversation_id,
                role,
                content,
                json.dumps(structured_data) if structured_data else None,
                json.dumps(claude_messages) if claude_messages else None,
                now,
            ),
        )
        # Touch conversation updated_at
        conn.execute(
            "UPDATE agent_conversations SET updated_at = ? WHERE conversation_id = ?",
            (now, conversation_id),
        )
        conn.commit()
    return message_id


def get_messages(conversation_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation, ordered by created_at ascending."""
    db = get_database()
    with db._get_connection() as conn:
        rows = conn.execute(
            """SELECT message_id, conversation_id, role, content, structured_data, claude_messages, created_at
               FROM agent_messages
               WHERE conversation_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
    messages = []
    for row in rows:
        msg = dict(row)
        if msg.get("structured_data"):
            try:
                msg["structured_data"] = json.loads(msg["structured_data"])
            except (json.JSONDecodeError, TypeError):
                pass
        if msg.get("claude_messages"):
            try:
                msg["claude_messages"] = json.loads(msg["claude_messages"])
            except (json.JSONDecodeError, TypeError):
                pass
        messages.append(msg)
    return messages


def get_message_count(conversation_id: str) -> int:
    """Get the number of messages in a conversation."""
    db = get_database()
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM agent_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return row["cnt"] if row else 0


def list_conversations(limit: int = 20) -> list[dict]:
    """List recent conversations with message counts."""
    db = get_database()
    with db._get_connection() as conn:
        rows = conn.execute(
            """SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                      COUNT(m.message_id) as message_count
               FROM agent_conversations c
               LEFT JOIN agent_messages m ON c.conversation_id = m.conversation_id
               GROUP BY c.conversation_id
               ORDER BY c.updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and all its messages."""
    db = get_database()
    with db._get_connection() as conn:
        # Delete messages first (FK cascade may not be enforced in SQLite by default)
        conn.execute(
            "DELETE FROM agent_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM agent_pending_actions WHERE conversation_id = ?",
            (conversation_id,),
        )
        cursor = conn.execute(
            "DELETE FROM agent_conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def conversation_exists(conversation_id: str) -> bool:
    """Check if a conversation exists."""
    db = get_database()
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM agent_conversations WHERE conversation_id = ?",
            (conversation_id,),
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
    """Save a pending action that requires user confirmation.

    Args:
        tool_use_id: The original tool_use block ID from Claude's response.
            Critical for building the matching tool_result when the user confirms/cancels.
    """
    db = get_database()
    # Store tool_use_id alongside tool_input for retrieval later
    stored_input = dict(tool_input)
    if tool_use_id:
        stored_input["_tool_use_id"] = tool_use_id

    with db._get_connection() as conn:
        # Clear any prior pending actions for this conversation
        conn.execute(
            "DELETE FROM agent_pending_actions WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            """INSERT INTO agent_pending_actions
               (action_id, conversation_id, tool_name, tool_input, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                action_id,
                conversation_id,
                tool_name,
                json.dumps(stored_input),
                description,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
    logger.info(f"Saved pending action {action_id} for conversation {conversation_id}: {tool_name}")


def get_pending_action(conversation_id: str, action_id: str) -> Optional[dict]:
    """Get a pending action by conversation and action ID.

    Extracts the stored _tool_use_id from tool_input and returns it as a
    top-level field for easy access by the orchestrator.
    """
    db = get_database()
    with db._get_connection() as conn:
        row = conn.execute(
            """SELECT action_id, conversation_id, tool_name, tool_input, description, created_at
               FROM agent_pending_actions
               WHERE conversation_id = ? AND action_id = ?""",
            (conversation_id, action_id),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["tool_input"] = json.loads(result["tool_input"])
    except (json.JSONDecodeError, TypeError):
        pass
    # Extract the stored tool_use_id and clean it from tool_input
    if isinstance(result.get("tool_input"), dict):
        result["tool_use_id"] = result["tool_input"].pop("_tool_use_id", None)
    else:
        result["tool_use_id"] = None
    return result


def clear_pending_actions(conversation_id: str) -> None:
    """Clear all pending actions for a conversation."""
    db = get_database()
    with db._get_connection() as conn:
        conn.execute(
            "DELETE FROM agent_pending_actions WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()
