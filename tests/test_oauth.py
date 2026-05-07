from tests.conftest import TEST_AGENT, TEST_KEY


async def test_token_endpoint_returns_jwt(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["expires_in"] > 0
    assert len(body["access_token"].split(".")) == 3


async def test_token_wrong_credentials(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": TEST_AGENT,
            "client_secret": "wrongkey",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_client"


async def test_token_wrong_grant_type(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


async def test_oauth_server_metadata(client):
    r = await client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert "token_endpoint" in body
    assert body["token_endpoint"].endswith("/oauth/token")
    assert "client_credentials" in body["grant_types_supported"]


async def test_bearer_token_authenticates_mcp_request(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": TEST_AGENT,
            "client_secret": TEST_KEY,
        },
    )
    token = r.json()["access_token"]
    r2 = await client.get(
        "/memory",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200


async def test_bearer_token_invalid_rejected(client):
    r = await client.get(
        "/memory",
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert r.status_code == 401


async def test_self_register_requires_key_when_configured(client):
    r = await client.post("/agents/self-register", json={"agent_id": "newagent"})
    assert r.status_code == 401


async def test_self_register_with_correct_key(client):
    r = await client.post(
        "/agents/self-register",
        json={"agent_id": "newagent"},
        headers={"x-registration-key": "regkey"},
    )
    assert r.status_code == 201
    assert r.json()["agent_id"] == "newagent"
