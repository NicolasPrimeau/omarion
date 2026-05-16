from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...store.db import get_db
from ..auth import ActorDep, ReaderDep
from ..config import settings
from ..models import ProjectInfo

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectMember(BaseModel):
    agent_id: str
    joined_at: str


class ProjectSummary(BaseModel):
    project_id: str
    joined_at: str


@router.post("/{project_id}/join", status_code=204, summary="Join a project")
async def join_project(project_id: str, agent_id: str = ActorDep):
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO project_members (project_id, agent_id) VALUES (?, ?)",
        (project_id, agent_id),
    )
    db.commit()


@router.delete("/{project_id}/leave", status_code=204, summary="Leave a project")
async def leave_project(project_id: str, agent_id: str = ActorDep):
    db = get_db()
    db.execute(
        "DELETE FROM project_members WHERE project_id=? AND agent_id=?",
        (project_id, agent_id),
    )
    db.commit()


@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMember],
    summary="List members of a project",
)
async def list_members(project_id: str, agent_id: str = ReaderDep):
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM project_members WHERE project_id=? AND agent_id=?",
        (project_id, agent_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="not a member of this project")
    rows = db.execute(
        "SELECT agent_id, joined_at FROM project_members WHERE project_id=? ORDER BY joined_at",
        (project_id,),
    ).fetchall()
    return [ProjectMember(agent_id=r["agent_id"], joined_at=r["joined_at"]) for r in rows]


@router.get("/mine", response_model=list[ProjectSummary], summary="List projects you belong to")
async def list_my_projects(agent_id: str = ReaderDep):
    db = get_db()
    rows = db.execute(
        "SELECT project_id, joined_at FROM project_members WHERE agent_id=? ORDER BY joined_at",
        (agent_id,),
    ).fetchall()
    return [ProjectSummary(project_id=r["project_id"], joined_at=r["joined_at"]) for r in rows]


@router.get("", response_model=list[ProjectInfo])
async def list_projects(agent_id: str = ReaderDep):
    db = get_db()

    projects: dict[str, dict] = {}

    def _ensure(name: str) -> dict:
        if name not in projects:
            projects[name] = {
                "agents": set(),
                "memory_count": 0,
                "task_count": 0,
                "last_activity": None,
            }
        return projects[name]

    for row in db.execute(
        "SELECT project, agent_id, COUNT(*) as cnt, MAX(updated_at) as last FROM memory "
        "WHERE project IS NOT NULL AND deleted_at IS NULL GROUP BY project, agent_id"
    ).fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["agent_id"])
        p["memory_count"] += row["cnt"]
        if not p["last_activity"] or row["last"] > p["last_activity"]:
            p["last_activity"] = row["last"]

    for row in db.execute(
        "SELECT project, created_by, COUNT(*) as cnt, MAX(updated_at) as last FROM tasks "
        "WHERE project IS NOT NULL GROUP BY project, created_by"
    ).fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["created_by"])
        p["task_count"] += row["cnt"]
        if not p["last_activity"] or row["last"] > p["last_activity"]:
            p["last_activity"] = row["last"]

    for row in db.execute("SELECT id, project FROM agents WHERE project IS NOT NULL").fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["id"])

    for agent_id_cfg, proj_list in settings.agent_projects().items():
        for proj in proj_list:
            p = _ensure(proj)
            p["agents"].add(agent_id_cfg)

    for row in db.execute("SELECT project_id, agent_id FROM project_members").fetchall():
        p = _ensure(row["project_id"])
        p["agents"].add(row["agent_id"])

    return [
        ProjectInfo(
            name=name,
            agents=sorted(data["agents"]),
            memory_count=data["memory_count"],
            task_count=data["task_count"],
            last_activity=data["last_activity"],
        )
        for name, data in sorted(projects.items())
    ]
