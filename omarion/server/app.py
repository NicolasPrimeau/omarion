from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..store.db import get_db
from .config import settings
from .routes.events import router as events_router
from .routes.memory import router as memory_router
from .routes.messages import router as messages_router
from .routes.sessions import router as sessions_router
from .routes.tasks import router as tasks_router

_UI = Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="Omarion", version="0.1.0")

app.include_router(memory_router)
app.include_router(tasks_router)
app.include_router(messages_router)
app.include_router(events_router)
app.include_router(sessions_router)


@app.on_event("startup")
async def startup():
    get_db(settings.db_path)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def ui():
    return _UI.read_text()
