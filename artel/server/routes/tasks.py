import json
import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ...store.db import get_db
from ..auth import _memberships, project_filter, require_agent
from ..models import (
    TaskAction,
    TaskComment,
    TaskCommentCreate,
    TaskCreate,
    TaskEntry,
    TaskUpdate,
    new_id,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _row_to_task(row: sqlite3.Row) -> TaskEntry:
    return TaskEntry(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        expected_outcome=row["expected_outcome"],
        status=row["status"],
        created_by=row["created_by"],
        assigned_to=row["assigned_to"],
        project=row["project"],
        priority=row["priority"],
        due_at=row["due_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_comment(row: sqlite3.Row) -> TaskComment:
    return TaskComment(
        id=row["id"],
        task_id=row["task_id"],
        agent_id=row["agent_id"],
        kind=row["kind"],
        body=row["body"],
        created_at=row["created_at"],
    )


def _emit_event(db: sqlite3.Connection, event_type: str, agent_id: str, payload: dict) -> None:
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), event_type, agent_id, json.dumps(payload)),
    )


def _add_comment(db: sqlite3.Connection, task_id: str, agent_id: str, kind: str, body: str) -> None:
    db.execute(
        "INSERT INTO task_comments (id, task_id, agent_id, kind, body) VALUES (?,?,?,?,?)",
        (new_id(), task_id, agent_id, kind, body),
    )


@router.get("/{task_id}", response_model=TaskEntry, summary="Get a task by ID")
async def get_task(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    return _row_to_task(row)


@router.post("", response_model=TaskEntry, status_code=201, summary="Create a task")
async def create_task(body: TaskCreate, agent_id: str = Depends(require_agent)):
    db = get_db()
    if body.project:
        allowed = _memberships(agent_id)
        if allowed is not None and body.project not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    task_id = new_id()
    with db:
        db.execute(
            """INSERT INTO tasks (id, title, description, expected_outcome, created_by,
               project, priority, assigned_to, due_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                body.title,
                body.description,
                body.expected_outcome,
                agent_id,
                body.project,
                body.priority,
                body.assigned_to,
                body.due_at,
            ),
        )
        _emit_event(db, "task.created", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.get("", response_model=list[TaskEntry], summary="List tasks with optional filters")
async def list_tasks(
    status: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    project: str | None = Query(default=None),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if project:
        allowed = _memberships(agent_id)
        if allowed is not None and project not in allowed:
            return []
        sql += " AND project=?"
        params.append(project)
    else:
        pf_clause, pf_params = project_filter(agent_id)
        if pf_clause:
            sql += f" AND {pf_clause}"
            params.extend(pf_params)
    if status:
        sql += " AND status=?"
        params.append(status)
    if agent:
        sql += " AND (created_by=? OR assigned_to=?)"
        params.extend([agent, agent])
    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql, params).fetchall()
    return [_row_to_task(r) for r in rows]


@router.post("/{task_id}/claim", response_model=TaskEntry, summary="Claim an open task")
async def claim_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    if not db.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        raise HTTPException(status_code=404, detail="not found")
    with db:
        cursor = db.execute(
            """UPDATE tasks SET status='claimed', assigned_to=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status='open'""",
            (agent_id, task_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=409, detail="task not open")
        _add_comment(db, task_id, agent_id, "claim", body.body)
        _emit_event(db, "task.claimed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/unclaim",
    response_model=TaskEntry,
    summary="Release your claim on a task (assignee only)",
)
async def unclaim_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail="task not claimed")
    if row["assigned_to"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    with db:
        db.execute(
            """UPDATE tasks SET status='open', assigned_to=NULL,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (task_id,),
        )
        _add_comment(db, task_id, agent_id, "unclaim", body.body)
        _emit_event(db, "task.unclaimed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/complete",
    response_model=TaskEntry,
    summary="Complete a claimed task (assignee only)",
)
async def complete_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail="task not claimed")
    if row["assigned_to"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    with db:
        db.execute(
            """UPDATE tasks SET status='completed',
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (task_id,),
        )
        _add_comment(db, task_id, agent_id, "complete", body.body)
        _emit_event(db, "task.completed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.patch(
    "/{task_id}", response_model=TaskEntry, summary="Update task title, description, or priority"
)
async def update_task(task_id: str, body: TaskUpdate, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] in ("completed", "failed"):
        raise HTTPException(status_code=409, detail="task is terminal and cannot be modified")
    set_parts: list[str] = []
    params: list = []
    if body.description is not None:
        if body.append:
            set_parts.append(
                "description = CASE WHEN description IS NOT NULL AND description != '' "
                "THEN description || ? ELSE ? END"
            )
            params.extend([f"\n\n---\n{body.description}", body.description])
        else:
            set_parts.append("description=?")
            params.append(body.description)
    if body.title is not None:
        set_parts.append("title=?")
        params.append(body.title)
    if body.priority is not None:
        set_parts.append("priority=?")
        params.append(body.priority)
    if body.expected_outcome is not None:
        set_parts.append("expected_outcome=?")
        params.append(body.expected_outcome)
    if set_parts:
        set_parts.append("updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        params.append(task_id)
        with db:
            db.execute(f"UPDATE tasks SET {', '.join(set_parts)} WHERE id=?", params)
            _emit_event(db, "task.updated", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/fail", response_model=TaskEntry, summary="Fail a claimed task (assignee only)"
)
async def fail_task(
    task_id: str,
    body: TaskAction = Body(default_factory=TaskAction),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "claimed":
        raise HTTPException(status_code=409, detail="task not claimed")
    if row["assigned_to"] != agent_id:
        raise HTTPException(status_code=403, detail="forbidden")
    with db:
        db.execute(
            """UPDATE tasks SET status='failed',
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (task_id,),
        )
        _add_comment(db, task_id, agent_id, "fail", body.body)
        _emit_event(db, "task.failed", agent_id, {"task_id": task_id})
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/comments",
    response_model=TaskComment,
    status_code=201,
    summary="Add a free-form comment to a task",
)
async def add_comment(
    task_id: str, body: TaskCommentCreate, agent_id: str = Depends(require_agent)
):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    comment_id = new_id()
    with db:
        db.execute(
            "INSERT INTO task_comments (id, task_id, agent_id, kind, body) VALUES (?,?,?,?,?)",
            (comment_id, task_id, agent_id, "comment", body.body),
        )
        _emit_event(db, "task.commented", agent_id, {"task_id": task_id, "comment_id": comment_id})
    crow = db.execute("SELECT * FROM task_comments WHERE id=?", (comment_id,)).fetchone()
    return _row_to_comment(crow)


@router.get(
    "/{task_id}/comments",
    response_model=list[TaskComment],
    summary="List comments and status events for a task",
)
async def list_comments(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["project"]:
        allowed = _memberships(agent_id)
        if allowed is not None and row["project"] not in allowed:
            raise HTTPException(status_code=403, detail="not a member of this project")
    rows = db.execute(
        "SELECT * FROM task_comments WHERE task_id=? ORDER BY created_at ASC",
        (task_id,),
    ).fetchall()
    return [_row_to_comment(r) for r in rows]
