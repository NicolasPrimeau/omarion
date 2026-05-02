import uvicorn

from .app import app
from .config import settings


def main():
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
