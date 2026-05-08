FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md llms.txt ./
RUN uv sync --frozen --no-dev

COPY artel/ artel/

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --no-create-home --uid 1000 artel && chown -R artel /app
USER artel

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

CMD ["python", "-m", "artel.server"]
