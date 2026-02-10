"""
Test script for the agent SSE endpoint.

Usage:
  From backend directory (with API running on localhost:8000):
    python scripts/test_agent_sse.py

  Against a different base URL:
    set API_BASE_URL=https://your-railway-url.up.railway.app
    python scripts/test_agent_sse.py

  With a specific question:
    python scripts/test_agent_sse.py "How many leads are in the database?"
"""

import json
import os
import sys

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
CHAT_URL = f"{API_BASE_URL}/api/agent/chat"


def test_agent_chat(message: str = "How many total leads are in the database?") -> None:
    """POST to agent chat and print each SSE event."""
    print(f"POST {CHAT_URL}")
    print(f"Body: {{ \"message\": {json.dumps(message)} }}")
    print("---")

    try:
        r = requests.post(
            CHAT_URL,
            json={"message": message},
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            stream=True,
            timeout=(10, 180),
        )
        r.raise_for_status()
    except requests.exceptions.Timeout:
        print("ERROR: Connection timeout (is the server running?)")
        return
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Connection failed: {e}")
        return
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP {e.response.status_code}: {e.response.text[:500]}")
        return

    current_event = ""
    current_data = ""
    event_count = 0

    for line in r.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            current_data = line[5:].strip()
        elif line == "" and current_event:
            event_count += 1
            try:
                parsed = json.loads(current_data)
            except json.JSONDecodeError:
                parsed = current_data
            print(f"[{event_count}] event={current_event} data={parsed!r}")
            if current_event == "error":
                print("  ^ ERROR - check backend logs and ANTHROPIC_API_KEY")
            if current_event == "done":
                print("--- DONE ---")
            current_event = ""
            current_data = ""

    print(f"Total events: {event_count}")
    if event_count == 0:
        print("WARNING: No SSE events received. Check backend logs and proxy buffering.")


if __name__ == "__main__":
    message = sys.argv[1] if len(sys.argv) > 1 else "How many total leads are in the database?"
    test_agent_chat(message)
