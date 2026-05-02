import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ...store.db import get_db
from ..auth import check_project, project_filter, require_agent
from ..models import TaskCreate, TaskEntry, new_id

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _row_to_task(row: sqlite3.Row) -> TaskEntry:
    return TaskEntry(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        created_by=row["created_by"],
        assigned_to=row["assigned_to"],
        project=row["project"],
        priority=row["priority"],
        due_at=row["due_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post("", response_model=TaskEntry, status_code=201)
async def create_task(body: TaskCreate, agent_id: str = Depends(require_agent)):
    check_project(agent_id, body.project)
    db = get_db()
    task_id = new_id()
    db.execute(
        """INSERT INTO tasks (id, title, description, created_by, project,
           priority, assigned_to, due_at) VALUES (?,?,?,?,?,?,?,?)""",
        (task_id, body.title, body.description, agent_id, body.project,
         body.priority, body.assigned_to, body.due_at),
    )
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), "task.created", agent_id, json.dumps({"task_id": task_id})),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.get("", response_model=list[TaskEntry])
async def list_tasks(
    status: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    agent_id: str = Depends(require_agent),
):
    db = get_db()
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
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


@router.post("/{task_id}/claim", response_model=TaskEntry)
async def claim_task(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    check_project(agent_id, row["project"])
    if row["status"] != "open":
        raise HTTPException(status_code=409, detail="task not open")
    db.execute(
        """UPDATE tasks SET status='claimed', assigned_to=?,
           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
        (agent_id, task_id),
    )
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), "task.claimed", agent_id, json.dumps({"task_id": task_id})),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post("/{task_id}/complete", response_model=TaskEntry)
async def complete_task(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    check_project(agent_id, row["project"])
    db.execute(
        """UPDATE tasks SET status='completed',
           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
        (task_id,),
    )
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), "task.completed", agent_id, json.dumps({"task_id": task_id})),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.post("/{task_id}/fail", response_model=TaskEntry)
async def fail_task(task_id: str, agent_id: str = Depends(require_agent)):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    check_project(agent_id, row["project"])
    db.execute(
        """UPDATE tasks SET status='failed',
           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
        (task_id,),
    )
    db.execute(
        "INSERT INTO events (id, type, agent_id, payload) VALUES (?,?,?,?)",
        (new_id(), "task.failed", agent_id, json.dumps({"task_id": task_id})),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(row)
