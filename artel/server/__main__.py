import uvicorn

from .config import settings


def main():
    uvicorn.run(
        "artel.server.app:app", host=settings.host, port=settings.port, reload=settings.reload
    )


if __name__ == "__main__":
    main()
