#!/bin/sh
set -e

# Generate mcp-agent credentials if AGENT_KEYS is not set.
# docker-compose deployments provide AGENT_KEYS via env_file; this
# only fires when the image is run directly (e.g. docker run, Glama).
if [ -z "$AGENT_KEYS" ]; then
    _key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    export AGENT_KEYS="mcp:${_key}"
    export MCP_AGENT_KEY="${_key}"
fi

# Forward REGISTRATION_KEY -> MCP_REGISTRATION_KEY when not set explicitly.
if [ -z "$MCP_REGISTRATION_KEY" ] && [ -n "$REGISTRATION_KEY" ]; then
    export MCP_REGISTRATION_KEY="$REGISTRATION_KEY"
fi

exec "$@"
