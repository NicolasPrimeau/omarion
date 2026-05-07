import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...store.db import get_db
from ..auth import project_filter, require_agent
from ..models import TaskCreate, TaskEntry, TaskUpdate, new_id

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


@router.get("/{task_id}", response_model=TaskEntry, summary="Get a task by ID")
async def get_task(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_task(row)


@router.post("", response_model=TaskEntry, status_code=201, summary="Create a task")
async def create_task(body: TaskCreate, agent_id: str = Depends(require_agent)):
    db = get_db()
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
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (new_id(), "task.created", agent_id, json.dumps({"task_id": task_id})),
        )
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
async def claim_task(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["status"] != "open":
        raise HTTPException(status_code=409, detail="task not open")
    with db:
        db.execute(
            """UPDATE tasks SET status='claimed', assigned_to=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (agent_id, task_id),
        )
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (new_id(), "task.claimed", agent_id, json.dumps({"task_id": task_id})),
        )
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/complete",
    response_model=TaskEntry,
    summary="Complete a claimed task (assignee only)",
)
async def complete_task(task_id: str, agent_id: str = Depends(require_agent)):
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
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (new_id(), "task.completed", agent_id, json.dumps({"task_id": task_id})),
        )
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
            db.execute(
                "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
                (new_id(), "task.updated", agent_id, json.dumps({"task_id": task_id})),
            )
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post(
    "/{task_id}/fail", response_model=TaskEntry, summary="Fail a claimed task (assignee only)"
)
async def fail_task(task_id: str, agent_id: str = Depends(require_agent)):
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
        db.execute(
            "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
            (new_id(), "task.failed", agent_id, json.dumps({"task_id": task_id})),
        )
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)
