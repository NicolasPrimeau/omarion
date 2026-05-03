import uvicorn

from .app import app
from .config import settings


def main():
    uvicorn.run("artel.server.app:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
