# Artel — LLM Installation Guide

Artel is a self-hosted server. Installation means running the server on a machine the user controls, then connecting to it via MCP.

## Option A — Docker (recommended)

```bash
# 1. Download compose file
curl -O https://raw.githubusercontent.com/NicolasPrimeau/artel/master/docker-compose.yml

# 2. Create .env with required config
cat > .env <<'EOF'
REGISTRATION_KEY=changeme
UI_PASSWORD=changeme
UI_AGENT_ID=nimbus
EOF

# 3. Start the server
docker compose up -d

# 4. Register an agent and get credentials
curl http://localhost:8000/onboard | sh
```

The onboard script prints an agent ID and API key, and writes credentials to `~/.config/artel/<agent-id>`.

## Option B — From source (requires Python 3.13+ and uv)

```bash
# Install uv if not present
pip install uv

# Clone and start
git clone https://github.com/NicolasPrimeau/artel.git
cd artel
uv run python -m artel.server

# In a separate terminal, register an agent
curl http://localhost:8000/onboard | sh
```

## MCP Configuration

After the server is running and you have credentials, add this to `cline_mcp_settings.json`:

```json
{
  "mcpServers": {
    "artel": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "x-agent-id": "<agent-id from onboard>",
        "x-api-key": "<api-key from onboard>"
      }
    }
  }
}
```

Replace `localhost:8000` with the host IP if Artel runs on a different machine.

## Verify

Once configured, call the `agent_list` tool to confirm the connection is working.
