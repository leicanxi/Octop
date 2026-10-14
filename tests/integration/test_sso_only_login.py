"""SSO-only mode: ``local_login_enabled`` hides the sign-in form.

Octop has no open sign-up — accounts come from the setup wizard, an admin, or an
invite — so hiding the form is what makes a deployment SSO-only in practice.
Invite redemption is the one route that is refused along with it, because it mints
a password account that the hidden form could never sign in again.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.app import octop_client, write_octop_config
from tests.support.auth import bootstrap_admin


async def _invite(c, auth: dict[str, str], note: str) -> str:
    created = await c.post(
        "/api/users/invites", headers=auth, json={"note": note, "expires_in_days": 7}
    )
    assert created.status_code == 201, created.text
    return str(created.json()["code"])


async def test_sign_in_form_config_reports_local_login_state(tmp_octop_home: Path) -> None:
    async with octop_client(tmp_octop_home) as (c, _srv):
        await bootstrap_admin(c, tmp_octop_home)
        opened = await c.get("/api/auth/captcha")
        assert opened.status_code == 200
        assert opened.json()["local_login_enabled"] is True

    write_octop_config(tmp_octop_home, local_login_enabled=False)
    async with octop_client(tmp_octop_home) as (c, _srv):
        closed = await c.get("/api/auth/captcha")
        assert closed.status_code == 200
        assert closed.json()["local_login_enabled"] is False


async def test_password_sign_in_route_stays_open_while_the_form_is_hidden(
    tmp_octop_home: Path,
) -> None:
    """The flag is presentation-only: the dashboard hides the form, this route works.

    Asserted on purpose, so the decision stays visible: tightening it into a
    server-side gate has to mean deleting this test, not discovering it.
    """
    async with octop_client(tmp_octop_home) as (c, _srv):
        await bootstrap_admin(c, tmp_octop_home, username="root", password="TestPass12")

    write_octop_config(tmp_octop_home, local_login_enabled=False)
    async with octop_client(tmp_octop_home) as (c, _srv):
        signed_in = await c.post(
            "/api/auth/login", json={"username": "root", "password": "TestPass12"}
        )
        assert signed_in.status_code == 200
        assert signed_in.json()["access_token"]


async def test_sso_only_refuses_invite_redemption(tmp_octop_home: Path) -> None:
    async with octop_client(tmp_octop_home) as (c, _srv):
        await bootstrap_admin(c, tmp_octop_home, username="root", password="TestPass12")
        tok = (
            await c.post("/api/auth/login", json={"username": "root", "password": "TestPass12"})
        ).json()["access_token"]
        code = await _invite(c, {"Authorization": f"Bearer {tok}"}, "carol")

    write_octop_config(tmp_octop_home, local_login_enabled=False)
    async with octop_client(tmp_octop_home) as (c, _srv):
        refused = await c.post(
            "/api/auth/invite/redeem",
            json={"code": code, "username": "carol", "password": "CarolPass123"},
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "LOCAL_LOGIN_DISABLED"


async def test_invite_redemption_works_while_the_form_is_open(tmp_octop_home: Path) -> None:
    async with octop_client(tmp_octop_home) as (c, _srv):
        await bootstrap_admin(c, tmp_octop_home, username="root", password="TestPass12")
        tok = (
            await c.post("/api/auth/login", json={"username": "root", "password": "TestPass12"})
        ).json()["access_token"]
        code = await _invite(c, {"Authorization": f"Bearer {tok}"}, "dave")

        redeemed = await c.post(
            "/api/auth/invite/redeem",
            json={"code": code, "username": "dave", "password": "DavePass123"},
        )
        assert redeemed.status_code == 200
        assert redeemed.json()["access_token"]
