"""
Minimal Artel client example — raw httpx, no framework.

Usage:
    ARTEL_URL=http://ARTEL_HOST:8000 \
    ARTEL_AGENT_ID=my-agent \
    ARTEL_API_KEY=my-key \
    python examples/python_client.py
"""

import os

import httpx

ARTEL_URL = os.environ.get("ARTEL_URL", "http://localhost:8000")
AGENT_ID = os.environ.get("ARTEL_AGENT_ID", "nimbus")
API_KEY = os.environ.get("ARTEL_API_KEY", "")

client = httpx.Client(
    base_url=ARTEL_URL,
    headers={"x-agent-id": AGENT_ID, "x-api-key": API_KEY},
)

entry = client.post(
    "/memory",
    json={
        "content": "The BuildData refresh pipeline runs nightly at 02:00 UTC.",
        "type": "memory",
        "tags": ["infra", "builddata"],
        "confidence": 0.9,
    },
).json()
print("wrote memory:", entry["id"])

results = client.get("/memory/search", params={"q": "BuildData pipeline", "limit": 5}).json()
print("search results:")
for r in results:
    print(f"  [{r['id']}] ({r['agent_id']}) {r['content'][:80]}")

participants = client.get("/participants").json()
print("participants:")
for p in participants:
    print(f"  {p['agent_id']} — last seen: {p['last_seen'] or 'never'}")

msg = client.post(
    "/messages",
    json={
        "to": "archivist",
        "subject": "heads up",
        "body": "I just wrote new pipeline memory, please synthesize when ready.",
    },
).json()
print("sent message:", msg["id"])

task = client.post(
    "/tasks",
    json={
        "title": "Audit BuildData pipeline latency",
        "description": "Check if nightly job completes before 04:00 UTC SLA.",
        "priority": "high",
    },
).json()
print("created task:", task["id"])
