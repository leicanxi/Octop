"""FlowBee (new-api) signed-handoff dashboard login adapter.

FlowBee cannot act as an OIDC issuer, so it does not fit ``/oidc/*``. Its fork
signs a short-lived HS256 ticket for the already-logged-in user and hands the
browser back to ``/api/auth/oauth/callback`` with that ticket in ``code``. We
verify the ticket with the shared secret and use it as the identity assertion.

Identity key is ``sub`` (the new-api user id), not the email: most accounts are
expected to own a stable id, while email is optional on FlowBee (and mutable, so
keying on it would strand the account after a change). Email/name are carried
along as profile hints only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from octop.infra.auth.sso.crypto import decrypt_secret
from octop.infra.auth.sso.public_base import oauth_callback_path
from octop.infra.db.repos.sso import SsoLoginStateRow, SsoProviderRow

if TYPE_CHECKING:
    from octop.infra.auth.sso.service import SsoService

_ALGORITHM = "HS256"
# FlowBee signs a short "long enough to follow one redirect" TTL; a minute of
# leeway absorbs clock skew in both directions.
_LEEWAY_SECONDS = 60
_REQUIRED_CLAIMS = ["exp", "iat"]


def flowbee_handoff_url(row: SsoProviderRow) -> str:
    """Return the configured FlowBee handoff page (e.g. ``https://host/sso/octop``)."""
    raw = row.extra.get("handoff_url")
    return raw.strip() if isinstance(raw, str) and raw.strip() else ""


def _validate_handoff_url(handoff: str) -> str:
    parsed = urlparse(handoff)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("FlowBee handoff URL must be an absolute http(s) URL")
    return handoff


def to_identity_claims(ticket: dict[str, Any]) -> dict[str, Any]:
    """Map ticket claims onto the keys ``UserManager`` reads for provisioning."""
    claims: dict[str, Any] = {}
    name = ticket.get("name")
    if isinstance(name, str) and name.strip():
        claims["name"] = name.strip()
    email = ticket.get("email")
    if isinstance(email, str) and email.strip():
        claims["email"] = email.strip()
    username = ticket.get("username")
    if isinstance(username, str) and username.strip():
        claims["preferred_username"] = username.strip()
    return claims


class FlowbeeAdapter:
    kind = "flowbee"
    callback_path = oauth_callback_path()
    default_scopes = ""

    def __init__(self, service: SsoService) -> None:
        self._service = service

    def is_configured(self, row: SsoProviderRow) -> bool:
        return bool(flowbee_handoff_url(row) and row.client_secret_enc is not None)

    def authorize_url(
        self,
        *,
        row: SsoProviderRow,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str:
        # No authorize endpoint on FlowBee: the state is echoed back by the
        # handoff page (which redirects to our callback) rather than by an IdP.
        del nonce, code_challenge, redirect_uri
        handoff = _validate_handoff_url(flowbee_handoff_url(row))
        separator = "&" if urlparse(handoff).query else "?"
        return f"{handoff}{separator}{urlencode({'state': state})}"

    def complete_login(
        self,
        code: str,
        *,
        row: SsoProviderRow,
        login_state: SsoLoginStateRow,
        redirect_uri: str,
    ) -> tuple[str, dict[str, Any]]:
        del redirect_uri, login_state
        if row.client_secret_enc is None:
            raise ValueError("FlowBee SSO secret is not configured")
        secret = decrypt_secret(self._service._services.secret_repo, row.client_secret_enc)
        ticket = jwt.decode(
            code,
            key=secret,
            algorithms=[_ALGORITHM],
            options={"require": _REQUIRED_CLAIMS},
            leeway=_LEEWAY_SECONDS,
        )
        subject = ticket.get("sub")
        if isinstance(subject, int):
            subject = str(subject)
        if not isinstance(subject, str) or not subject.strip():
            raise jwt.InvalidTokenError("FlowBee ticket has no subject")
        return subject.strip(), to_identity_claims(ticket)

    def test_connection(self, row: SsoProviderRow) -> dict[str, bool | str]:
        if row.client_secret_enc is None:
            return {"ok": False, "detail": "FlowBee SSO secret is not configured"}
        handoff = flowbee_handoff_url(row)
        if not handoff:
            return {"ok": False, "detail": "FlowBee handoff URL is not configured"}
        try:
            _validate_handoff_url(handoff)
        except ValueError as exc:
            return {"ok": False, "detail": str(exc)}
        # The secret itself is only provable by a ticket FlowBee signs on first
        # login; here we can still catch a typo'd host / missing page.
        try:
            with self._service._http_client() as client:
                response = client.get(handoff, follow_redirects=False)
        except httpx.HTTPError as exc:
            return {"ok": False, "detail": str(exc)}
        if response.status_code >= 400:
            return {
                "ok": False,
                "detail": f"FlowBee handoff returned HTTP {response.status_code}",
            }
        return {"ok": True, "detail": "FlowBee handoff URL is reachable"}
