"""
Claude tool-use orchestrator with SSE streaming.

The orchestrator is an async generator that yields SSE-formatted events
as the conversation progresses. This is the core of the agent.
"""

import json
import logging
import os
import time
from typing import AsyncGenerator, Optional
from uuid import uuid4

import anthropic

from src.agent.system_prompt import SYSTEM_PROMPT
from src.agent.types import (
    TOOL_SCHEMAS,
    AgentConfirmationPayload,
    SSEEventType,
)
from src.agent.tools import execute_tool
from src.agent import memory

logger = logging.getLogger(__name__)

# Write tools that require user confirmation before execution
WRITE_TOOLS = {"update_leads_batch", "enrich_leads_batch"}

# Max tool-use rounds per conversation turn
MAX_ROUNDS = 10

# Max messages before truncation
MAX_MESSAGES = 50

# Active conversations — prevents concurrent requests to same conversation
_active_conversations: set[str] = set()


def _sse_event(event_type: str, data) -> dict:
    """Format an SSE event as a dict for sse-starlette."""
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data)
    return {"event": event_type, "data": payload}


def _describe_action(tool_name: str, tool_input: dict) -> str:
    """Generate a human-readable description of a pending action."""
    lead_count = len(tool_input.get("lead_ids", []))
    if tool_name == "update_leads_batch":
        parts = []
        if tool_input.get("pipeline_stage"):
            parts.append(f"move to '{tool_input['pipeline_stage']}' stage")
        if tool_input.get("priority_rank") is not None:
            parts.append(f"set priority to {tool_input['priority_rank']}")
        if tool_input.get("next_follow_up"):
            parts.append(f"set follow-up to {tool_input['next_follow_up']}")
        action = ", ".join(parts) if parts else "update"
        return f"{action.capitalize()} for {lead_count} lead{'s' if lead_count != 1 else ''}"
    elif tool_name == "enrich_leads_batch":
        return f"Start enrichment for {lead_count} lead{'s' if lead_count != 1 else ''}"
    return f"Execute {tool_name} on {lead_count} items"


def _build_claude_messages(conversation_id: str) -> list[dict]:
    """Load conversation history and build the Claude messages array."""
    messages = memory.get_messages(conversation_id)
    claude_messages = []

    for msg in messages:
        # If we have raw Claude messages stored, use them directly
        if msg.get("claude_messages") and isinstance(msg["claude_messages"], list):
            claude_messages.extend(msg["claude_messages"])
        else:
            # Fallback: reconstruct from content
            claude_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

    # Truncate if too long — keep first 2 + last (MAX_MESSAGES - 2)
    if len(claude_messages) > MAX_MESSAGES:
        claude_messages = claude_messages[:2] + claude_messages[-(MAX_MESSAGES - 2):]

    return claude_messages


async def run_agent(
    message: str,
    conversation_id: Optional[str] = None,
    confirmation: Optional[AgentConfirmationPayload] = None,
) -> AsyncGenerator[dict, None]:
    """
    Main agent loop. Yields SSE event dicts.

    Flow:
    1. Load or create conversation
    2. If confirmation: execute pending action, then call Claude for response
    3. Otherwise: append user message, enter tool-use loop
    4. Save everything to memory
    """
    # 1. Load or create conversation
    if conversation_id and memory.conversation_exists(conversation_id):
        pass  # Use existing
    else:
        conversation_id = memory.create_conversation()

    # Concurrency guard
    if conversation_id in _active_conversations:
        yield _sse_event(SSEEventType.error, "Another request is already processing for this conversation.")
        yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
        return
    _active_conversations.add(conversation_id)

    try:
        # Initialize Anthropic client
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            yield _sse_event(SSEEventType.error, "Anthropic API key not configured.")
            yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
            return

        agent_model = os.environ.get("AGENT_MODEL", "claude-sonnet-4-20250514")
        client = anthropic.Anthropic(api_key=api_key)

        # 2. Handle confirmation flow
        if confirmation:
            yield _sse_event(SSEEventType.status, "Processing confirmation...")
            await _handle_confirmation(
                client, agent_model, conversation_id, confirmation, _sse_event_gen=None
            )
            # Use a separate generator approach for confirmations
            async for event in _handle_confirmation_flow(
                client, agent_model, conversation_id, confirmation
            ):
                yield event
            return

        # 3. Normal message flow
        yield _sse_event(SSEEventType.status, "Thinking...")

        # Build messages from history
        claude_messages = _build_claude_messages(conversation_id)

        # Add user message
        claude_messages.append({"role": "user", "content": message})

        # Save user message to memory
        memory.add_message(
            conversation_id,
            "user",
            message,
            claude_messages=[{"role": "user", "content": message}],
        )

        # Auto-title the conversation from the first message
        if len(claude_messages) == 1:
            title = message[:80] + ("..." if len(message) > 80 else "")
            memory.update_conversation_title(conversation_id, title)

        # Tool-use loop
        round_count = 0
        accumulated_text = ""
        accumulated_structured = {}
        accumulated_claude_msgs = []  # Raw Claude messages for this turn

        while round_count < MAX_ROUNDS:
            round_count += 1
            start_time = time.time()

            try:
                response = client.messages.create(
                    model=agent_model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_SCHEMAS,
                    messages=claude_messages,
                )
            except anthropic.APIError as e:
                if e.status_code == 429:
                    # Rate limited — retry once
                    yield _sse_event(SSEEventType.status, "Rate limited, retrying...")
                    import asyncio
                    await asyncio.sleep(5)
                    try:
                        response = client.messages.create(
                            model=agent_model,
                            max_tokens=4096,
                            system=SYSTEM_PROMPT,
                            tools=TOOL_SCHEMAS,
                            messages=claude_messages,
                        )
                    except Exception as retry_err:
                        yield _sse_event(SSEEventType.error, "AI service temporarily unavailable. Try again in a minute.")
                        break
                else:
                    yield _sse_event(SSEEventType.error, f"AI service error: {str(e)}")
                    break
            except Exception as e:
                yield _sse_event(SSEEventType.error, f"Request failed: {str(e)}")
                break

            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"Claude API call: model={agent_model}, "
                f"input_tokens={response.usage.input_tokens}, "
                f"output_tokens={response.usage.output_tokens}, "
                f"duration={duration_ms}ms, round={round_count}"
            )

            # Process response content blocks
            tool_calls_this_round = []
            tool_results_this_round = []
            assistant_content = []

            for block in response.content:
                if block.type == "text":
                    accumulated_text += block.text
                    yield _sse_event(SSEEventType.partial_text, block.text)
                    assistant_content.append({"type": "text", "text": block.text})

                elif block.type == "tool_use":
                    # Confirmation gate: intercept write tools
                    if block.name in WRITE_TOOLS:
                        action_id = str(uuid4())
                        description = _describe_action(block.name, block.input)
                        count = len(block.input.get("lead_ids", []))

                        yield _sse_event(SSEEventType.needs_confirmation, {
                            "action_id": action_id,
                            "description": description,
                            "count": count,
                        })

                        # Save pending action
                        memory.save_pending_action(
                            conversation_id, action_id, block.name, block.input, description
                        )

                        # Save the assistant's response up to this point (including the tool_use block)
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                        accumulated_claude_msgs.append({
                            "role": "assistant",
                            "content": assistant_content,
                        })

                        # Save conversation state
                        memory.add_message(
                            conversation_id,
                            "assistant",
                            accumulated_text,
                            structured_data=accumulated_structured if accumulated_structured else None,
                            claude_messages=accumulated_claude_msgs,
                        )

                        yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
                        return  # Stop — wait for confirmation

                    # Read-only tools: execute immediately
                    yield _sse_event(SSEEventType.status, f"Running {block.name}...")
                    yield _sse_event(SSEEventType.tool_call, {"name": block.name, "input": block.input})

                    try:
                        result = execute_tool(block.name, block.input)
                        is_error = "error" in result and len(result) == 1
                    except Exception as e:
                        result = {"error": str(e)}
                        is_error = True

                    # Yield structured data for frontend rendering
                    if block.name == "query_leads" and "leads" in result:
                        yield _sse_event(SSEEventType.leads, result["leads"])
                        accumulated_structured["leads"] = result["leads"]
                        accumulated_structured["filters_applied"] = result.get("filters_applied", {})
                    elif block.name == "generate_cold_call_scripts" and "scripts" in result:
                        yield _sse_event(SSEEventType.scripts, result["scripts"])
                        accumulated_structured["scripts"] = result["scripts"]
                    elif block.name == "compile_email_briefing" and "html" in result:
                        yield _sse_event(SSEEventType.briefing_preview, {
                            "briefing_id": result.get("briefing_id", ""),
                            "html": result.get("html", ""),
                            "lead_count": result.get("lead_count", 0),
                        })
                        accumulated_structured["briefing"] = {
                            "briefing_id": result.get("briefing_id"),
                            "lead_count": result.get("lead_count"),
                        }
                    elif block.name == "refine_rent_estimates" and "comparisons" in result:
                        yield _sse_event(SSEEventType.rent_comparison, result["comparisons"])
                        accumulated_structured["rent_comparisons"] = result["comparisons"]

                    # Build tool_use + tool_result for Claude
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_calls_this_round.append(block)
                    tool_results_this_round.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)[:10000],  # Truncate large results
                        **({"is_error": True} if is_error else {}),
                    })

            # Append assistant response + tool results to messages for next round
            if assistant_content:
                claude_messages.append({"role": "assistant", "content": assistant_content})
                accumulated_claude_msgs.append({"role": "assistant", "content": assistant_content})

            if tool_results_this_round:
                claude_messages.append({"role": "user", "content": tool_results_this_round})
                accumulated_claude_msgs.append({"role": "user", "content": tool_results_this_round})

            # If no tool calls and stop_reason == "end_turn", we're done
            if response.stop_reason == "end_turn":
                break

        # Loop limit reached
        if round_count >= MAX_ROUNDS:
            yield _sse_event(SSEEventType.error, "I hit my processing limit. Here's what I found so far.")

        # Save assistant message to memory
        memory.add_message(
            conversation_id,
            "assistant",
            accumulated_text,
            structured_data=accumulated_structured if accumulated_structured else None,
            claude_messages=accumulated_claude_msgs if accumulated_claude_msgs else None,
        )

        yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})

    finally:
        _active_conversations.discard(conversation_id)


async def _handle_confirmation_flow(
    client: anthropic.Anthropic,
    agent_model: str,
    conversation_id: str,
    confirmation: AgentConfirmationPayload,
) -> AsyncGenerator[dict, None]:
    """Handle a confirmation or cancellation response."""
    try:
        action = memory.get_pending_action(conversation_id, confirmation.action_id)
        if not action:
            yield _sse_event(SSEEventType.error, "Pending action not found or expired.")
            yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
            return

        if confirmation.confirmed:
            # Execute the pending tool
            yield _sse_event(SSEEventType.status, f"Executing {action['tool_name']}...")
            result = execute_tool(action["tool_name"], action["tool_input"])

            actions_taken = result.get("actions", [action["description"]])
            yield _sse_event(SSEEventType.actions, actions_taken)

            # Save confirmation as user message
            memory.add_message(
                conversation_id,
                "user",
                f"Confirmed: {action['description']}",
                claude_messages=[{"role": "user", "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(uuid4()),  # Synthetic ID
                        "content": json.dumps(result),
                    }
                ]}],
            )
        else:
            # User cancelled
            memory.add_message(
                conversation_id,
                "user",
                f"Cancelled: {action['description']}",
                claude_messages=[{"role": "user", "content": f"User cancelled the action: {action['description']}"}],
            )

        # Clear pending action
        memory.clear_pending_actions(conversation_id)

        # Call Claude to generate a follow-up response
        yield _sse_event(SSEEventType.status, "Generating response...")
        claude_messages = _build_claude_messages(conversation_id)

        try:
            response = client.messages.create(
                model=agent_model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=claude_messages,
            )

            accumulated_text = ""
            for block in response.content:
                if block.type == "text":
                    accumulated_text += block.text
                    yield _sse_event(SSEEventType.partial_text, block.text)

            # Save assistant response
            memory.add_message(
                conversation_id,
                "assistant",
                accumulated_text,
                claude_messages=[{"role": "assistant", "content": [{"type": "text", "text": accumulated_text}]}],
            )

        except Exception as e:
            yield _sse_event(SSEEventType.error, f"Failed to generate response: {str(e)}")

        yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})

    finally:
        _active_conversations.discard(conversation_id)
