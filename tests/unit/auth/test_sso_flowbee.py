"""Tests for the FlowBee (new-api) signed-handoff SSO adapter."""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import jwt
import pytest

from octop.config import OctopConfig
from octop.infra.auth.sso.providers.flowbee import FlowbeeAdapter
from octop.infra.auth.sso.service import SsoService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.sso import SsoLoginStateRow, SsoProviderRow
from octop.infra.db.services import build_shared_services
from octop.infra.users.manager import UserManager
from octop.infra.utils.paths import PathLayout

SECRET = "flowbee-workspace-secret-0123456789"
HANDOFF_URL = "https://flowbee.example/sso/octop"
CALLBACK = "https://octop.example/api/auth/oauth/callback"


@pytest.fixture
def service(tmp_path):
    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    return SsoService(services, UserManager(services))


def provider_row(
    *, handoff_url: str = HANDOFF_URL, secret: bytes | None = b"ciphertext"
) -> SsoProviderRow:
    extra = {"handoff_url": handoff_url} if handoff_url else {}
    return SsoProviderRow(
        id=1,
        enabled=1,
        display_name="FlowBee",
        issuer="",
        client_id="",
        client_secret_enc=secret,
        scopes="",
        dashboard_origin=None,
        created_at=0,
        updated_at=0,
        kind="flowbee",
        extra=extra,
    )


def configured(service: SsoService, *, handoff_url: str = HANDOFF_URL) -> SsoProviderRow:
    service.put_config_for_kind(
        "flowbee",
        {
            "enabled": True,
            "display_name": "FlowBee",
            "client_secret": SECRET,
            "extra": {"handoff_url": handoff_url},
        },
    )
    row = service._services.sso_repo.get_by_kind("flowbee")
    assert row is not None
    return row


def ticket(**overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "42",
        "username": "leicanxi",
        "email": "user@example.com",
        "name": "Leicanxi",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def login_state() -> SsoLoginStateRow:
    now = int(time.time())
    return SsoLoginStateRow(
        state="state",
        provider_id=1,
        nonce="nonce",
        code_verifier="verifier",
        redirect_after="/chat",
        login_code=None,
        user_id=None,
        expires_at=now + 600,
        consumed_at=None,
        created_at=now,
    )


def mock_http_client(handler):
    real_client = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    return patch("octop.infra.auth.sso.service.httpx.Client", side_effect=factory)


def test_is_configured_needs_both_handoff_url_and_secret(service: SsoService) -> None:
    adapter = FlowbeeAdapter(service)
    assert adapter.is_configured(provider_row()) is True
    assert adapter.is_configured(provider_row(handoff_url="   ")) is False
    assert adapter.is_configured(provider_row(secret=None)) is False


def test_authorize_url_lands_on_handoff_page_with_state(service: SsoService) -> None:
    adapter = FlowbeeAdapter(service)
    url = adapter.authorize_url(
        row=provider_row(),
        state="browser-state",
        nonce="nonce",
        code_challenge="challenge",
        redirect_uri=CALLBACK,
    )
    assert url == f"{HANDOFF_URL}?state=browser-state"
    assert adapter.callback_path == "/api/auth/oauth/callback"


def test_authorize_url_appends_to_existing_query_and_rejects_relative(
    service: SsoService,
) -> None:
    adapter = FlowbeeAdapter(service)
    url = adapter.authorize_url(
        row=provider_row(handoff_url=f"{HANDOFF_URL}?tenant=acme"),
        state="s",
        nonce="",
        code_challenge="",
        redirect_uri=CALLBACK,
    )
    assert url == f"{HANDOFF_URL}?tenant=acme&state=s"

    with pytest.raises(ValueError, match="absolute"):
        adapter.authorize_url(
            row=provider_row(handoff_url="/sso/octop"),
            state="s",
            nonce="",
            code_challenge="",
            redirect_uri=CALLBACK,
        )


def test_complete_login_verifies_ticket_and_maps_identity(service: SsoService) -> None:
    adapter = FlowbeeAdapter(service)
    row = configured(service)
    subject, claims = adapter.complete_login(
        ticket(), row=row, login_state=login_state(), redirect_uri=CALLBACK
    )
    assert subject == "42"
    assert claims == {
        "name": "Leicanxi",
        "email": "user@example.com",
        "preferred_username": "leicanxi",
    }


def test_complete_login_survives_a_user_without_email(service: SsoService) -> None:
    """~40% of FlowBee accounts have no email; the id is the identity key."""
    adapter = FlowbeeAdapter(service)
    row = configured(service)
    subject, claims = adapter.complete_login(
        ticket(email=None),
        row=row,
        login_state=login_state(),
        redirect_uri=CALLBACK,
    )
    assert subject == "42"
    assert claims == {"name": "Leicanxi", "preferred_username": "leicanxi"}


@pytest.mark.parametrize(
    "bad_ticket",
    [
        pytest.param(lambda: ticket(exp=int(time.time()) - 600), id="expired"),
        pytest.param(lambda: ticket(sub=""), id="empty-subject"),
        pytest.param(lambda: ticket(sub=None), id="missing-subject"),
        pytest.param(
            lambda: jwt.encode(
                {"sub": "42"}, "another-workspace-secret-0123456789", algorithm="HS256"
            ),
            id="wrong-secret",
        ),
        pytest.param(
            lambda: jwt.encode(
                {"sub": "42", "exp": int(time.time()) + 300}, SECRET, algorithm="HS512"
            ),
            id="wrong-algorithm",
        ),
        pytest.param(lambda: "not-a-ticket", id="malformed"),
    ],
)
def test_complete_login_rejects_unusable_tickets(service: SsoService, bad_ticket) -> None:
    adapter = FlowbeeAdapter(service)
    row = configured(service)
    with pytest.raises(jwt.InvalidTokenError):
        adapter.complete_login(
            bad_ticket(), row=row, login_state=login_state(), redirect_uri=CALLBACK
        )


def test_put_config_normalizes_handoff_url_and_blanks_issuer(service: SsoService) -> None:
    config = service.put_config_for_kind(
        "flowbee",
        {
            "enabled": True,
            "issuer": "https://ignored.example",
            "client_secret": SECRET,
            "extra": {"handoff_url": f"  {HANDOFF_URL}  "},
        },
        public_base="https://octop.example",
    )
    assert config["kind"] == "flowbee"
    assert config["extra"] == {"handoff_url": HANDOFF_URL}
    assert config["issuer"] == ""
    assert config["display_name"] == "FlowBee"
    assert config["has_client_secret"] is True
    assert config["redirect_uri"] == CALLBACK


def test_put_config_defaults_display_name(service: SsoService) -> None:
    config = service.put_config_for_kind(
        "flowbee",
        {"client_secret": SECRET, "extra": {"handoff_url": HANDOFF_URL}},
        public_base="https://octop.example",
    )
    assert config["display_name"] == "FlowBee"


def test_empty_secret_keeps_the_previous_value(service: SsoService) -> None:
    first = configured(service)
    service.put_config_for_kind(
        "flowbee", {"client_secret": "", "extra": {"handoff_url": HANDOFF_URL}}
    )
    second = service._services.sso_repo.get_by_kind("flowbee")
    assert second is not None
    assert second.client_secret_enc == first.client_secret_enc


def test_flowbee_login_button_appears_once_a_user_exists(service: SsoService) -> None:
    configured(service, handoff_url=HANDOFF_URL)
    kinds = {provider["kind"]: provider for provider in service.providers_status()["providers"]}
    assert "flowbee" in kinds
    assert kinds["flowbee"]["enabled"] is False  # No local user yet.

    service._services.user_repo.create(username="admin", role="admin")
    enabled = {provider["kind"]: provider for provider in service.providers_status()["providers"]}
    assert enabled["flowbee"]["enabled"] is True
    assert enabled["flowbee"]["display_name"] == "FlowBee"


def test_test_connection_reports_missing_configuration(service: SsoService) -> None:
    adapter = FlowbeeAdapter(service)
    assert adapter.test_connection(provider_row(secret=None))["ok"] is False
    assert adapter.test_connection(provider_row(handoff_url=""))["ok"] is False
    assert adapter.test_connection(provider_row(handoff_url="not-a-url"))["ok"] is False


def test_test_connection_probes_the_handoff_page(service: SsoService) -> None:
    adapter = FlowbeeAdapter(service)
    row = configured(service)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<html></html>")

    with mock_http_client(handler):
        assert adapter.test_connection(row)["ok"] is True
    assert seen == [HANDOFF_URL]

    with mock_http_client(lambda request: httpx.Response(404)):
        result = adapter.test_connection(row)
    assert result["ok"] is False
    assert "404" in str(result["detail"])
