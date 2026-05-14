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


async def test_unclaim_task(client):
    r = await client.post("/tasks", json={"title": "unclaimable"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/unclaim", headers=HEADERS)
    assert r2.status_code == 200
    t = r2.json()
    assert t["status"] == "open"
    assert t["assigned_to"] is None


async def test_unclaim_records_comment_with_body(client):
    r = await client.post("/tasks", json={"title": "with reason"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)
    await client.post(
        f"/tasks/{tid}/unclaim", json={"body": "waiting on external review"}, headers=HEADERS
    )

    r3 = await client.get(f"/tasks/{tid}/comments", headers=HEADERS)
    comments = r3.json()
    kinds = [c["kind"] for c in comments]
    assert "claim" in kinds and "unclaim" in kinds
    unclaim = next(c for c in comments if c["kind"] == "unclaim")
    assert unclaim["body"] == "waiting on external review"
    assert unclaim["agent_id"] == TEST_AGENT


async def test_unclaim_by_non_assignee_forbidden(client):
    r = await client.post("/tasks", json={"title": "owned"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/unclaim", headers=HEADERS2)
    assert r2.status_code == 403


async def test_unclaim_unclaimed_task_rejected(client):
    r = await client.post("/tasks", json={"title": "still open"}, headers=HEADERS)
    tid = r.json()["id"]

    r2 = await client.post(f"/tasks/{tid}/unclaim", headers=HEADERS)
    assert r2.status_code == 409


async def test_unclaim_then_reclaim_by_different_agent(client):
    r = await client.post("/tasks", json={"title": "passable"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)
    await client.post(f"/tasks/{tid}/unclaim", headers=HEADERS)

    r2 = await client.post(f"/tasks/{tid}/claim", headers=HEADERS2)
    assert r2.status_code == 200
    assert r2.json()["assigned_to"] == "otheragent"


async def test_add_comment(client):
    r = await client.post("/tasks", json={"title": "commentable"}, headers=HEADERS)
    tid = r.json()["id"]

    r2 = await client.post(
        f"/tasks/{tid}/comments", json={"body": "found a clue in logs"}, headers=HEADERS
    )
    assert r2.status_code == 201
    cmt = r2.json()
    assert cmt["kind"] == "comment"
    assert cmt["body"] == "found a clue in logs"
    assert cmt["agent_id"] == TEST_AGENT


async def test_list_comments_chronological(client):
    r = await client.post("/tasks", json={"title": "logged"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/comments", json={"body": "first"}, headers=HEADERS)
    await client.post(f"/tasks/{tid}/comments", json={"body": "second"}, headers=HEADERS2)
    await client.post(f"/tasks/{tid}/claim", json={"body": "picking up"}, headers=HEADERS)
    await client.post(f"/tasks/{tid}/complete", json={"body": "shipped"}, headers=HEADERS)

    r2 = await client.get(f"/tasks/{tid}/comments", headers=HEADERS)
    assert r2.status_code == 200
    comments = r2.json()
    assert [c["kind"] for c in comments] == ["comment", "comment", "claim", "complete"]
    assert [c["body"] for c in comments] == ["first", "second", "picking up", "shipped"]


async def test_lifecycle_ops_without_body_still_log(client):
    r = await client.post("/tasks", json={"title": "bare lifecycle"}, headers=HEADERS)
    tid = r.json()["id"]
    await client.post(f"/tasks/{tid}/claim", headers=HEADERS)
    await client.post(f"/tasks/{tid}/fail", headers=HEADERS)

    r2 = await client.get(f"/tasks/{tid}/comments", headers=HEADERS)
    comments = r2.json()
    assert [c["kind"] for c in comments] == ["claim", "fail"]
    assert all(c["body"] == "" for c in comments)


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


async def test_reopen_completed_task(client):
    r = await client.post("/tasks", json={"title": "to reopen"}, headers=HEADERS)
    task_id = r.json()["id"]
    await client.post(f"/tasks/{task_id}/claim", headers=HEADERS)
    await client.post(f"/tasks/{task_id}/complete", headers=HEADERS)

    r = await client.post(f"/tasks/{task_id}/reopen", json={}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["status"] == "open"
    assert r.json()["assigned_to"] is None


async def test_reopen_then_claim(client):
    r = await client.post("/tasks", json={"title": "reopen then claim"}, headers=HEADERS)
    task_id = r.json()["id"]
    await client.post(f"/tasks/{task_id}/claim", headers=HEADERS)
    await client.post(f"/tasks/{task_id}/complete", headers=HEADERS)
    await client.post(f"/tasks/{task_id}/reopen", json={}, headers=HEADERS)

    r = await client.post(f"/tasks/{task_id}/claim", headers=HEADERS2)
    assert r.status_code == 200
    assert r.json()["status"] == "claimed"
    assert r.json()["assigned_to"] == HEADERS2["x-agent-id"]


async def test_reopen_non_terminal_rejected(client):
    r = await client.post("/tasks", json={"title": "still open"}, headers=HEADERS)
    task_id = r.json()["id"]

    r = await client.post(f"/tasks/{task_id}/reopen", json={}, headers=HEADERS)
    assert r.status_code == 409


async def test_reopen_forbidden_for_non_creator(client):
    r = await client.post("/tasks", json={"title": "not yours"}, headers=HEADERS)
    task_id = r.json()["id"]
    await client.post(f"/tasks/{task_id}/claim", headers=HEADERS)
    await client.post(f"/tasks/{task_id}/complete", headers=HEADERS)

    r = await client.post(f"/tasks/{task_id}/reopen", json={}, headers=HEADERS2)
    assert r.status_code == 403


async def test_patch_completed_task_allowed(client):
    r = await client.post("/tasks", json={"title": "done task"}, headers=HEADERS)
    task_id = r.json()["id"]
    await client.post(f"/tasks/{task_id}/claim", headers=HEADERS)
    await client.post(f"/tasks/{task_id}/complete", headers=HEADERS)

    r = await client.patch(
        f"/tasks/{task_id}", json={"description": "post-completion note"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert r.json()["description"] == "post-completion note"
