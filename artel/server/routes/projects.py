from fastapi import APIRouter, Depends

from ...store.db import get_db
from ..auth import require_agent
from ..config import settings
from ..models import ProjectInfo

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectInfo])
async def list_projects(agent_id: str = Depends(require_agent)):
    db = get_db()

    projects: dict[str, dict] = {}

    def _ensure(name: str) -> dict:
        if name not in projects:
            projects[name] = {"agents": set(), "memory_count": 0, "task_count": 0, "last_activity": None}
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

    for row in db.execute(
        "SELECT id, project FROM agents WHERE project IS NOT NULL"
    ).fetchall():
        p = _ensure(row["project"])
        p["agents"].add(row["id"])

    for agent_id_cfg, proj_list in settings.agent_projects().items():
        for proj in proj_list:
            p = _ensure(proj)
            p["agents"].add(agent_id_cfg)

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
