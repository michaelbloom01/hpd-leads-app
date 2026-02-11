"""
Claude tool-use orchestrator with SSE streaming.

The orchestrator is an async generator that yields SSE-formatted events
as the conversation progresses. This is the core of the agent.
"""

import asyncio
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
    """Format an SSE event as a dict for sse-starlette. Always JSON-encode so frontend can parse consistently."""
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
    """Load conversation history and build the Claude messages array.

    Validates message structure to prevent Claude API 400 errors:
    - Messages must alternate user/assistant
    - tool_result blocks must follow an assistant message with matching tool_use
    - Consecutive same-role messages are merged
    """
    messages = memory.get_messages(conversation_id)
    claude_messages = []

    for msg in messages:
        # If we have raw Claude messages stored, use them directly
        if msg.get("claude_messages") and isinstance(msg["claude_messages"], list):
            claude_messages.extend(msg["claude_messages"])
        else:
            # Fallback: reconstruct from content
            if msg.get("content"):
                claude_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

    # Validate: ensure alternating roles; merge consecutive same-role messages
    validated = []
    for cm in claude_messages:
        if not cm.get("role") or not cm.get("content"):
            continue  # Skip empty/invalid messages
        if validated and validated[-1]["role"] == cm["role"]:
            # Merge: append content
            prev = validated[-1]
            if isinstance(prev["content"], str) and isinstance(cm["content"], str):
                prev["content"] = prev["content"] + "\n" + cm["content"]
            elif isinstance(prev["content"], list) and isinstance(cm["content"], list):
                prev["content"] = prev["content"] + cm["content"]
            elif isinstance(prev["content"], str) and isinstance(cm["content"], list):
                prev["content"] = [{"type": "text", "text": prev["content"]}] + cm["content"]
            elif isinstance(prev["content"], list) and isinstance(cm["content"], str):
                prev["content"] = prev["content"] + [{"type": "text", "text": cm["content"]}]
        else:
            validated.append(cm)

    # Ensure first message is from user (Claude requirement)
    while validated and validated[0]["role"] != "user":
        validated.pop(0)

    # Truncate if too long — keep first 2 + last (MAX_MESSAGES - 2)
    if len(validated) > MAX_MESSAGES:
        validated = validated[:2] + validated[-(MAX_MESSAGES - 2):]

    return validated


async def _call_claude(client, agent_model, system, tools, messages, timeout_secs, max_tokens=4096):
    """Call Claude API in a thread pool with timeout. Returns response or raises."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            client.messages.create,
            model=agent_model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        ),
        timeout=timeout_secs + 10,
    )


async def _call_claude_safe(client, agent_model, messages, timeout_secs, max_tokens=4096):
    """Call Claude with full error handling. Returns (response, error_event_or_none)."""
    try:
        response = await _call_claude(
            client, agent_model, SYSTEM_PROMPT, TOOL_SCHEMAS, messages, timeout_secs, max_tokens
        )
        return response, None
    except asyncio.TimeoutError:
        logger.warning("Agent: Claude call timed out")
        return None, _sse_event(SSEEventType.error, "Request timed out. Try a simpler query.")
    except anthropic.APIError as e:
        if e.status_code == 429:
            return None, "rate_limited"  # Sentinel for retry
        logger.error(f"Agent: Claude API error: {e}")
        return None, _sse_event(SSEEventType.error, f"AI service error: {str(e)}")
    except Exception as e:
        err_lower = str(e).lower()
        if "timeout" in err_lower or "timed out" in err_lower:
            return None, _sse_event(SSEEventType.error, "Request timed out. Try a simpler query.")
        logger.error(f"Agent: unexpected error: {e}", exc_info=True)
        return None, _sse_event(SSEEventType.error, f"Request failed: {str(e)}")


async def _tool_use_loop(
    client,
    agent_model: str,
    timeout_secs: float,
    claude_messages: list[dict],
    conversation_id: str,
) -> AsyncGenerator[dict, None]:
    """Core tool-use loop. Yields SSE events. Modifies claude_messages in place.

    Returns the accumulated text, structured data, and claude message history
    through the yielded events. Also yields a special _internal_result event
    at the end with the accumulated state.
    """
    round_count = 0
    accumulated_text = ""
    accumulated_structured = {}
    accumulated_claude_msgs = []

    while round_count < MAX_ROUNDS:
        round_count += 1
        start_time = time.time()
        logger.info(f"Agent: round {round_count}, calling Claude (timeout={timeout_secs}s)")

        response, error = await _call_claude_safe(client, agent_model, claude_messages, timeout_secs)

        # Handle rate limiting with one retry
        if error == "rate_limited":
            yield _sse_event(SSEEventType.status, "Rate limited, retrying...")
            await asyncio.sleep(5)
            response, error = await _call_claude_safe(client, agent_model, claude_messages, timeout_secs)
            if error == "rate_limited":
                error = _sse_event(SSEEventType.error, "AI service temporarily unavailable. Try again in a minute.")

        if error and isinstance(error, dict):
            yield error
            break
        if response is None:
            yield _sse_event(SSEEventType.error, "Failed to get a response. Try again.")
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
        needs_confirmation = False

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

                    # Save pending action WITH the original tool_use block ID
                    memory.save_pending_action(
                        conversation_id, action_id, block.name, block.input,
                        description, tool_use_id=block.id,
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

                    # Save conversation state and stop
                    memory.add_message(
                        conversation_id,
                        "assistant",
                        accumulated_text,
                        structured_data=accumulated_structured if accumulated_structured else None,
                        claude_messages=accumulated_claude_msgs,
                    )

                    yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
                    needs_confirmation = True
                    break  # Exit the for loop

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

        # If confirmation was triggered, stop entirely (done event already yielded)
        if needs_confirmation:
            return

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
        timeout_secs = float(os.environ.get("AGENT_CLAUDE_TIMEOUT", "120"))
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout_secs)

        # 2. Handle confirmation flow
        if confirmation:
            yield _sse_event(SSEEventType.status, "Processing confirmation...")
            async for event in _handle_confirmation_flow(
                client, agent_model, timeout_secs, conversation_id, confirmation
            ):
                yield event
            return

        # 3. Normal message flow
        yield _sse_event(SSEEventType.status, "Thinking...")
        logger.info("Agent: yielded status Thinking..., building messages")

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

        # Run the tool-use loop
        async for event in _tool_use_loop(
            client, agent_model, timeout_secs, claude_messages, conversation_id
        ):
            yield event

    finally:
        _active_conversations.discard(conversation_id)


async def _handle_confirmation_flow(
    client: anthropic.Anthropic,
    agent_model: str,
    timeout_secs: float,
    conversation_id: str,
    confirmation: AgentConfirmationPayload,
) -> AsyncGenerator[dict, None]:
    """Handle a confirmation or cancellation response.

    Key design: the previous assistant message ended with a tool_use block.
    Claude REQUIRES the next user message to contain a matching tool_result.
    We must use the ORIGINAL tool_use block's ID, not a synthetic one.
    """
    action = memory.get_pending_action(conversation_id, confirmation.action_id)
    if not action:
        yield _sse_event(SSEEventType.error, "Pending action not found or expired.")
        yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
        return

    # Get the original tool_use block ID that Claude generated
    original_tool_use_id = action.get("tool_use_id")
    if not original_tool_use_id:
        # Fallback: try to find it from the last assistant message's claude_messages
        logger.warning("No tool_use_id stored in pending action, attempting recovery")
        messages = memory.get_messages(conversation_id)
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("claude_messages"):
                cm_list = msg["claude_messages"] if isinstance(msg["claude_messages"], list) else []
                for cm in cm_list:
                    if cm.get("role") == "assistant" and isinstance(cm.get("content"), list):
                        for block in cm["content"]:
                            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == action["tool_name"]:
                                original_tool_use_id = block.get("id")
                                break
                    if original_tool_use_id:
                        break
            if original_tool_use_id:
                break

    if not original_tool_use_id:
        yield _sse_event(SSEEventType.error, "Could not find the original tool request. Please start a new message.")
        yield _sse_event(SSEEventType.done, {"conversation_id": conversation_id})
        return

    if confirmation.confirmed:
        # Execute the pending tool
        yield _sse_event(SSEEventType.status, f"Executing {action['tool_name']}...")
        try:
            result = execute_tool(action["tool_name"], action["tool_input"])
        except Exception as e:
            result = {"error": str(e)}

        actions_taken = result.get("actions", [action["description"]])
        yield _sse_event(SSEEventType.actions, actions_taken)

        # Save the tool_result as a user message with the CORRECT tool_use_id
        tool_result_content = [{
            "type": "tool_result",
            "tool_use_id": original_tool_use_id,
            "content": json.dumps(result)[:10000],
        }]
        memory.add_message(
            conversation_id,
            "user",
            f"Confirmed: {action['description']}",
            claude_messages=[{"role": "user", "content": tool_result_content}],
        )
    else:
        # User cancelled — send a tool_result with is_error indicating cancellation
        # Claude requires a tool_result after every tool_use, even on cancel
        tool_result_content = [{
            "type": "tool_result",
            "tool_use_id": original_tool_use_id,
            "content": json.dumps({"cancelled": True, "message": f"User cancelled: {action['description']}"}),
            "is_error": True,
        }]
        memory.add_message(
            conversation_id,
            "user",
            f"Cancelled: {action['description']}",
            claude_messages=[{"role": "user", "content": tool_result_content}],
        )

    # Clear pending action
    memory.clear_pending_actions(conversation_id)

    # Now call Claude for follow-up — use the FULL tool-use loop so Claude can
    # call more tools (e.g., generate scripts after enrichment confirmation)
    yield _sse_event(SSEEventType.status, "Generating response...")
    claude_messages = _build_claude_messages(conversation_id)

    async for event in _tool_use_loop(
        client, agent_model, timeout_secs, claude_messages, conversation_id
    ):
        yield event
