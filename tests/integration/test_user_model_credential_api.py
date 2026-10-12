"""tests/integration/test_user_model_credential_api.py"""

from __future__ import annotations


async def _login(c, username: str) -> dict[str, str]:
    tok = (
        await c.post("/api/auth/login", json={"username": username, "password": "TestPass12"})
    ).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


async def _make_user(c, admin_auth: dict[str, str], username: str) -> None:
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": username, "password": "TestPass12", "role": "user"},
    )


async def test_credential_roundtrip(env):
    """PUT/GET/DELETE /providers/my-credential stores only the caller's own pair."""
    c, _, auth = env

    r = await c.put(
        "/api/providers/my-credential",
        headers=auth,
        json={"base_url": "https://mine.example/v1", "api_key": "sk-mine"},
    )
    assert r.status_code == 200
    assert r.json() == {"base_url": "https://mine.example/v1", "has_api_key": True}

    r = await c.get("/api/providers/my-credential", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"base_url": "https://mine.example/v1", "has_api_key": True}

    r = await c.delete("/api/providers/my-credential", headers=auth)
    assert r.status_code == 204

    r = await c.get("/api/providers/my-credential", headers=auth)
    assert r.json() == {"base_url": None, "has_api_key": False}


async def test_credential_never_echoes_the_key(env):
    """The stored API key is never returned by the read endpoint."""
    c, _, auth = env
    await c.put(
        "/api/providers/my-credential",
        headers=auth,
        json={"base_url": "https://mine.example/v1", "api_key": "sk-secret-value"},
    )

    r = await c.get("/api/providers/my-credential", headers=auth)

    assert "sk-secret-value" not in r.text


async def test_credentials_are_per_user(env):
    """One user's credentials are invisible to another."""
    c, _, admin_auth = env
    await _make_user(c, admin_auth, "alice")
    await _make_user(c, admin_auth, "bob")
    alice = await _login(c, "alice")
    bob = await _login(c, "bob")

    await c.put(
        "/api/providers/my-credential",
        headers=alice,
        json={"base_url": "https://alice.example/v1", "api_key": "sk-alice"},
    )

    r = await c.get("/api/providers/my-credential", headers=bob)
    assert r.json() == {"base_url": None, "has_api_key": False}


async def test_empty_payload_clears_credentials(env):
    """Submitting empty fields removes the stored row."""
    c, _, auth = env
    await c.put(
        "/api/providers/my-credential",
        headers=auth,
        json={"base_url": "https://mine.example/v1", "api_key": "sk-mine"},
    )

    r = await c.put("/api/providers/my-credential", headers=auth, json={})

    assert r.json() == {"base_url": None, "has_api_key": False}
