from tests.conftest import HEADERS, HEADERS2, TEST_AGENT


async def test_create_task(client):
    r = await client.post(
        "/tasks",
        json={"title": "fix the bug", "description": "it crashes", "priority": "high"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    task = r.json()
    assert task["title"] == "fix the bug"
    assert task["status"] == "open"
    assert task["priority"] == "high"


async def test_list_tasks(client):
    await client.post("/tasks", json={"title": "task one"}, headers=HEADERS)
    await client.post("/tasks", json={"title": "task two"}, headers=HEADERS)

    r = await client.get("/tasks", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_list_tasks_by_status(client):
    await client.post("/tasks", json={"title": "open task"}, headers=HEADERS)

    r = await client.get("/tasks", params={"status": "open"}, headers=HEADERS)
    assert r.status_code == 200
    assert all(t["status"] == "open" for t in r.json())


async def test_claim_task(client):
    r = await client.post("/tasks", json={"title": "claimable"}, headers=HEADERS)
    tid = r.json()["id"]

    r2 = await client.post(f"/tasks/{tid}/claim", headers=HEADERS2)
    assert r2.status_code == 200
    assert r2.json()["status"] == "claimed"
    assert r2.json()["assigned_to"] == "otheragent"


async def test_complete_task(client):
    r = await client.post("/tasks", json={"title": "completable"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/complete", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["status"] == "completed"


async def test_fail_task(client):
    r = await client.post("/tasks", json={"title": "failable"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/fail", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["status"] == "failed"


async def test_get_task_by_id(client):
    r = await client.post(
        "/tasks", json={"title": "get me", "description": "details here"}, headers=HEADERS
    )
    tid = r.json()["id"]

    r2 = await client.get(f"/tasks/{tid}", headers=HEADERS)
    assert r2.status_code == 200
    t = r2.json()
    assert t["id"] == tid
    assert t["title"] == "get me"
    assert t["description"] == "details here"
    assert t["created_by"] == TEST_AGENT


async def test_get_task_not_found(client):
    r = await client.get("/tasks/00000000-0000-0000-0000-000000000000", headers=HEADERS)
    assert r.status_code == 404


async def test_list_tasks_by_project(client):
    await client.post("/projects/alpha/join", headers=HEADERS)
    await client.post("/tasks", json={"title": "alpha task", "project": "alpha"}, headers=HEADERS)
    await client.post("/tasks", json={"title": "beta task", "project": "beta"}, headers=HEADERS)
    await client.post("/tasks", json={"title": "no project task"}, headers=HEADERS)

    r = await client.get("/tasks", params={"project": "alpha"}, headers=HEADERS)
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "alpha task"


async def test_complete_task_by_non_assignee_forbidden(client):
    r = await client.post("/tasks", json={"title": "owned"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/complete", headers=HEADERS2)
    assert r2.status_code == 403


async def test_fail_task_by_non_assignee_forbidden(client):
    r = await client.post("/tasks", json={"title": "owned"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/fail", headers=HEADERS2)
    assert r2.status_code == 403


async def test_complete_unclaimed_task_rejected(client):
    r = await client.post("/tasks", json={"title": "open only"}, headers=HEADERS)
    tid = r.json()["id"]

    r2 = await client.post(f"/tasks/{tid}/complete", headers=HEADERS)
    assert r2.status_code == 409


async def test_update_task(client):
    r = await client.post("/tasks", json={"title": "original title"}, headers=HEADERS)
    tid = r.json()["id"]

    r2 = await client.patch(
        f"/tasks/{tid}",
        json={"title": "updated title", "description": "progress notes", "priority": "high"},
        headers=HEADERS,
    )
    assert r2.status_code == 200
    t = r2.json()
    assert t["title"] == "updated title"
    assert t["description"] == "progress notes"
    assert t["priority"] == "high"


async def test_update_task_append(client):
    r = await client.post(
        "/tasks", json={"title": "log task", "description": "initial notes"}, headers=HEADERS
    )
    tid = r.json()["id"]

    r2 = await client.patch(
        f"/tasks/{tid}", json={"description": "progress update", "append": True}, headers=HEADERS
    )
    assert r2.status_code == 200
    assert r2.json()["description"] == "initial notes\n\n---\nprogress update"

    r3 = await client.patch(
        f"/tasks/{tid}", json={"description": "second update", "append": True}, headers=HEADERS
    )
    assert r3.json()["description"] == "initial notes\n\n---\nprogress update\n\n---\nsecond update"


async def test_update_task_append_empty_existing(client):
    r = await client.post("/tasks", json={"title": "fresh task"}, headers=HEADERS)
    tid = r.json()["id"]

    r2 = await client.patch(
        f"/tasks/{tid}", json={"description": "first note", "append": True}, headers=HEADERS
    )
    assert r2.status_code == 200
    assert r2.json()["description"] == "first note"


async def test_task_lifecycle(client):
    r = await client.post("/tasks", json={"title": "lifecycle"}, headers=HEADERS)
    tid = r.json()["id"]

    r_list = await client.get("/tasks", params={"status": "open"}, headers=HEADERS)
    assert any(t["id"] == tid for t in r_list.json())

    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)
    r_list2 = await client.get("/tasks", params={"status": "claimed"}, headers=HEADERS)
    assert any(t["id"] == tid for t in r_list2.json())

    await client.post(f"/tasks/{tid}/complete", headers=HEADERS)
    r_list3 = await client.get("/tasks", params={"status": "completed"}, headers=HEADERS)
    assert any(t["id"] == tid for t in r_list3.json())
