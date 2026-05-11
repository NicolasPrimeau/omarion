FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md llms.txt ./
RUN uv sync --frozen --no-dev

COPY artel/ artel/
COPY supervisord.conf entrypoint.sh ./

ENV PATH="/app/.venv/bin:$PATH"

# All-in-one defaults — override per-service in docker-compose
ENV MCP_AGENT_ID="mcp" \
    ARTEL_URL="http://localhost:8000" \
    MCP_TRANSPORT="streamable-http" \
    MCP_PORT="8001"

RUN useradd --no-create-home --uid 1000 artel && chown -R artel /app && chmod +x /app/entrypoint.sh
USER artel

EXPOSE 8000 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["supervisord", "-c", "/app/supervisord.conf"]
