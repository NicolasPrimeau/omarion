import uvicorn

from .config import settings


def main():
    uvicorn.run(
        "artel.server.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
