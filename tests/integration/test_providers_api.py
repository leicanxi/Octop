"""tests/integration/test_providers_api.py"""

from __future__ import annotations


async def test_list_providers(env):
    """GET /providers returns all providers (read-only endpoint for any user)."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "openai", "kind": "openai", "api_key": "k"},
    )
    assert r.status_code == 201

    r = await c.get("/api/providers", headers=auth)
    assert r.status_code == 200
    assert any(p["name"] == "openai" for p in r.json())


async def test_admin_creates_provider(env):
    """POST /admin/providers creates a provider."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "openai", "kind": "openai", "api_key": "k"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "openai"
    assert "user_id" not in body


async def test_regular_user_cannot_create_provider(env):
    """POST /admin/providers is admin-only."""
    c, _, admin_auth = env
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "regular", "password": "TestPass12", "role": "user"},
    )
    tok = (
        await c.post("/api/auth/login", json={"username": "regular", "password": "TestPass12"})
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {tok}"}

    r = await c.post(
        "/api/admin/providers",
        headers=user_auth,
        json={"name": "should-fail", "kind": "openai"},
    )
    assert r.status_code == 403


async def test_admin_delete_provider(env):
    """DELETE /admin/providers/{id} removes the provider."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "to-delete", "kind": "openai"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code in (200, 204)

    r = await c.get("/api/admin/providers", headers=auth)
    ids = [p["id"] for p in r.json()]
    assert pid not in ids


async def test_admin_cannot_delete_local_runtime_provider(env):
    """DELETE /admin/providers/{id} keeps ONNX / Ollama local rows."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "ONNX (Local)", "kind": "openai", "api_key": "onnx"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_LOCAL_PROTECTED"

    r = await c.get("/api/admin/providers", headers=auth)
    ids = [p["id"] for p in r.json()]
    assert pid in ids


async def test_public_provider_list_never_exposes_api_key(env):
    """GET /providers is open to any logged-in user but must not leak the key."""
    c, _, admin_auth = env
    await c.post(
        "/api/admin/providers",
        headers=admin_auth,
        json={"name": "with-key", "kind": "openai", "api_key": "sk-shared-secret"},
    )
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "peeker", "password": "TestPass12", "role": "user"},
    )
    tok = (
        await c.post("/api/auth/login", json={"username": "peeker", "password": "TestPass12"})
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {tok}"}

    r = await c.get("/api/providers", headers=user_auth)

    assert r.status_code == 200
    assert "sk-shared-secret" not in r.text
    target = next(p for p in r.json() if p["name"] == "with-key")
    assert "api_key" not in target


async def test_admin_provider_list_still_returns_api_key(env):
    """The admin endpoint (the one the settings UI reads) keeps returning the key."""
    c, _, auth = env
    await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "with-key", "kind": "openai", "api_key": "sk-admin-visible"},
    )

    r = await c.get("/api/admin/providers", headers=auth)

    assert r.status_code == 200
    assert any(p.get("api_key") == "sk-admin-visible" for p in r.json())
