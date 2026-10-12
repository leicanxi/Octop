"""User model credential middleware tests."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from octop.infra.agents.middleware.user_model_credentials import (
    UserModelCredentialMiddleware,
)
from octop.infra.agents.providers.user_credentials import UserModelCredential


def _plain(value: Any) -> str | None:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


class _FakeClient:
    """Mimics the httpx client langchain builds from the model's credentials."""

    def __init__(self, *, api_key: Any, base_url: str | None) -> None:
        self.api_key = _plain(api_key)
        self.base_url = base_url


class _FakeModel:
    """Mimics ``ChatOpenAI`` closely enough to reproduce langchain's credential trap.

    Two langchain behaviours are mirrored here, because they are exactly why the
    middleware rebuilds the model instead of copying it:

    * ``root_client`` is built during construction, from the credentials;
    * ``model_copy`` copies that already-built client verbatim, so a copied
      model keeps calling the original endpoint (a silent failure).

    The assertions below pin the middleware to rebuilding, so a regression back
    to ``model_copy`` fails loudly rather than billing the shared provider.
    """

    model_fields = {
        "openai_api_key": None,
        "openai_api_base": None,
        "temperature": None,
        "root_client": None,
    }

    def __init__(
        self,
        *,
        openai_api_key: Any = None,
        openai_api_base: str | None = None,
        temperature: float = 0.0,
        root_client: _FakeClient | None = None,
    ) -> None:
        self.openai_api_key = openai_api_key
        self.openai_api_base = openai_api_base
        self.temperature = temperature
        self.root_client = root_client or _FakeClient(
            api_key=openai_api_key, base_url=openai_api_base
        )

    def model_copy(self, *, update: dict[str, Any]) -> _FakeModel:
        values = {name: getattr(self, name) for name in self.model_fields}
        values.update(update)
        # langchain replays the copied client rather than rebuilding it.
        return _FakeModel(**values)

    @property
    def api_key_value(self) -> str | None:
        return _plain(self.openai_api_key)


class _NonOpenAIModel:
    """A model that has no OpenAI credential fields (e.g. Anthropic)."""

    def __init__(self) -> None:
        self.anthropic_api_key = "sk-ant"


class _FakeRequest:
    def __init__(self, model: Any) -> None:
        self.model = model

    def override(self, *, model: Any) -> _FakeRequest:
        return _FakeRequest(model)


def _through_middleware(model: Any) -> Any:
    """Run *model* through the middleware and return the model the handler saw."""
    seen: dict[str, Any] = {}

    def handler(request: _FakeRequest) -> str:
        seen["model"] = request.model
        return "ok"

    middleware = UserModelCredentialMiddleware(
        credential_repo=object(),
        secret_repo=object(),
    )
    assert middleware.wrap_model_call(_FakeRequest(model), handler) == "ok"
    return seen["model"]


def _patch(monkeypatch: pytest.MonkeyPatch, credential: UserModelCredential | None) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.configurable_user_id",
        lambda: 7,
    )
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.load_user_credentials",
        lambda *_args: credential,
    )


def test_overrides_credentials_and_rebuilds_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, UserModelCredential(base_url="https://mine/v1", api_key="sk-mine"))
    shared = _FakeModel(openai_api_key="sk-shared", openai_api_base="https://shared/v1")

    model = _through_middleware(shared)

    # A new instance, not the shared one.
    assert model is not shared
    # Fields carry the user's credentials...
    assert model.api_key_value == "sk-mine"
    assert model.openai_api_base == "https://mine/v1"
    # ...and the client was rebuilt from them (the point of the whole exercise).
    assert model.root_client is not shared.root_client
    assert model.root_client.api_key == "sk-mine"
    assert model.root_client.base_url == "https://mine/v1"
    # The shared model is left untouched.
    assert shared.api_key_value == "sk-shared"
    assert shared.root_client.base_url == "https://shared/v1"


def test_keeps_shared_model_when_user_has_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, None)
    shared = _FakeModel(openai_api_key="sk-shared", openai_api_base="https://shared/v1")

    assert _through_middleware(shared) is shared


def test_partial_credential_only_overrides_provided_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, UserModelCredential(base_url=None, api_key="sk-mine"))

    model = _through_middleware(
        _FakeModel(openai_api_key="sk-shared", openai_api_base="https://shared/v1")
    )

    assert model.api_key_value == "sk-mine"
    assert model.openai_api_base == "https://shared/v1"
    assert model.root_client.api_key == "sk-mine"
    assert model.root_client.base_url == "https://shared/v1"


def test_skips_non_openai_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, UserModelCredential(base_url="https://mine/v1", api_key="sk-mine"))
    model = _NonOpenAIModel()

    assert _through_middleware(model) is model


def test_skips_when_no_acting_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.configurable_user_id",
        lambda: None,
    )
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.load_user_credentials",
        lambda *_args: pytest.fail("must not look up credentials without a user"),
    )
    shared = _FakeModel(openai_api_key="sk-shared", openai_api_base="https://shared/v1")

    assert _through_middleware(shared) is shared


@pytest.mark.asyncio
async def test_async_path_overrides_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, UserModelCredential(base_url="https://mine/v1", api_key="sk-mine"))
    seen: dict[str, Any] = {}

    async def handler(request: _FakeRequest) -> str:
        seen["model"] = request.model
        return "ok"

    middleware = UserModelCredentialMiddleware(
        credential_repo=object(),
        secret_repo=object(),
    )
    result = await middleware.awrap_model_call(
        _FakeRequest(_FakeModel(openai_api_key="sk-shared", openai_api_base="https://shared/v1")),
        handler,
    )

    assert result == "ok"
    assert seen["model"].api_key_value == "sk-mine"
    assert seen["model"].root_client.base_url == "https://mine/v1"
