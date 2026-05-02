"""
Artel + AutoGen example — wraps Artel memory and messaging as AutoGen tools.

Install: pip install pyautogen httpx
Usage:
    ARTEL_URL=http://ARTEL_HOST:8000 \
    ARTEL_AGENT_ID=autogen-agent \
    ARTEL_API_KEY=my-key \
    OPENAI_API_KEY=sk-... \
    python examples/autogen_agent.py
"""

import os

import httpx

try:
    import autogen
except ImportError:
    raise SystemExit("Install pyautogen: pip install pyautogen")

ARTEL_URL = os.environ.get("ARTEL_URL", "http://localhost:8000")
AGENT_ID = os.environ.get("ARTEL_AGENT_ID", "autogen-agent")
API_KEY = os.environ.get("ARTEL_API_KEY", "")

_http = httpx.Client(
    base_url=ARTEL_URL,
    headers={"x-agent-id": AGENT_ID, "x-api-key": API_KEY},
)


def artel_remember(content: str, tags: str = "") -> str:
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    r = _http.post("/memory", json={"content": content, "type": "memory", "tags": tag_list})
    r.raise_for_status()
    return f"stored: {r.json()['id']}"


def artel_recall(query: str) -> str:
    results = _http.get("/memory/search", params={"q": query, "limit": 5}).json()
    if not results:
        return "Nothing found."
    return "\n".join(f"[{e['id']}] {e['content'][:200]}" for e in results)


def artel_message(to: str, body: str) -> str:
    r = _http.post("/messages", json={"to": to, "body": body})
    r.raise_for_status()
    return f"sent: {r.json()['id']}"


llm_config = {
    "config_list": [{"model": "gpt-4o-mini", "api_key": os.environ.get("OPENAI_API_KEY", "")}],
    "functions": [
        {
            "name": "artel_remember",
            "description": "Write a fact to shared agent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "tags": {"type": "string", "description": "comma-separated tags"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "artel_recall",
            "description": "Search shared agent memory by semantic similarity.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "artel_message",
            "description": "Send a message to another agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "recipient agent_id"},
                    "body": {"type": "string"},
                },
                "required": ["to", "body"],
            },
        },
    ],
}

assistant = autogen.AssistantAgent(
    name="artel_assistant",
    llm_config=llm_config,
    function_map={
        "artel_remember": artel_remember,
        "artel_recall": artel_recall,
        "artel_message": artel_message,
    },
)

user = autogen.UserProxyAgent(
    name="user",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=3,
    function_map={
        "artel_remember": artel_remember,
        "artel_recall": artel_recall,
        "artel_message": artel_message,
    },
)

if __name__ == "__main__":
    user.initiate_chat(
        assistant,
        message=(
            "Search shared memory for anything about the BuildData pipeline. "
            "If you find something, summarize it and send a message to archivist "
            "with your summary. If nothing is found, store a note that BuildData "
            "pipeline docs are missing."
        ),
    )
