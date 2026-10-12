"""Bring-your-own-key gate: non-admin users must store their own model key.

Separate from ``test_user_model_credentials_middleware`` (which pins the
credential *swap*): these cases only exercise the policy decision, so they use
a lightweight model stub instead of the langchain client trap.
"""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.agents.middleware.user_model_credentials import (
    UserModelCredentialMiddleware,
)
from octop.infra.agents.providers.user_credentials import UserModelCredential
from octop.infra.errors import ErrorCode, OctopError


class _OpenAIModel:
    """OpenAI credential fields plus a rebuild-tolerant constructor."""

    model_fields = {"openai_api_key": None, "openai_api_base": None}

    def __init__(
        self,
        *,
        openai_api_key: Any = "sk-shared",
        openai_api_base: Any = "https://shared/v1",
    ) -> None:
        self.openai_api_key = openai_api_key
        self.openai_api_base = openai_api_base


class _AnthropicModel:
    """A model with no OpenAI credential fields (e.g. Anthropic)."""

    def __init__(self) -> None:
        self.anthropic_api_key = "sk-ant"


class _Request:
    def __init__(self, model: Any) -> None:
        self.model = model

    def override(self, *, model: Any) -> _Request:
        return _Request(model)


class _UserRow:
    def __init__(self, role: str) -> None:
        self.role = role


class _Users:
    """Minimal ``user_repo``; ``role=None`` stands for a missing row."""

    def __init__(self, role: str | None) -> None:
        self._role = role

    def get(self, user_id: int) -> _UserRow | None:
        del user_id
        return None if self._role is None else _UserRow(self._role)


def _patch(monkeypatch: pytest.MonkeyPatch, credential: UserModelCredential | None) -> None:
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.configurable_user_id",
        lambda: 7,
    )
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.load_user_credentials",
        lambda *_args: credential,
    )


def _middleware(*, role: str | None, require: bool = True) -> UserModelCredentialMiddleware:
    return UserModelCredentialMiddleware(
        credential_repo=object(),
        secret_repo=object(),
        require_credential=require,
        user_repo=_Users(role),
    )


def _run(middleware: UserModelCredentialMiddleware, model: Any) -> str:
    def handler(request: _Request) -> str:
        del request
        return "ok"

    return middleware.wrap_model_call(_Request(model), handler)


def test_refuses_a_non_admin_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, None)

    with pytest.raises(OctopError) as exc:
        _run(_middleware(role="user"), _OpenAIModel())

    assert exc.value.code is ErrorCode.MODEL_CREDENTIAL_REQUIRED


def test_admin_keeps_using_the_shared_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, None)

    assert _run(_middleware(role="admin"), _OpenAIModel()) == "ok"


def test_gate_is_off_when_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, None)

    assert _run(_middleware(role="user", require=False), _OpenAIModel()) == "ok"


def test_gate_covers_non_openai_protocols(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shared-key Anthropic call is refused too — checked before the swap."""
    _patch(monkeypatch, None)

    with pytest.raises(OctopError) as exc:
        _run(_middleware(role="user"), _AnthropicModel())

    assert exc.value.code is ErrorCode.MODEL_CREDENTIAL_REQUIRED


def test_base_url_only_credential_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored endpoint without a key is not a key of one's own."""
    _patch(monkeypatch, UserModelCredential(base_url="https://mine/v1", api_key=None))

    with pytest.raises(OctopError) as exc:
        _run(_middleware(role="user"), _OpenAIModel())

    assert exc.value.code is ErrorCode.MODEL_CREDENTIAL_REQUIRED


def test_own_key_satisfies_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, UserModelCredential(base_url=None, api_key="sk-mine"))

    assert _run(_middleware(role="user"), _OpenAIModel()) == "ok"


def test_missing_user_row_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """No row to judge → fail open rather than lock the account out."""
    _patch(monkeypatch, None)

    assert _run(_middleware(role=None), _OpenAIModel()) == "ok"


def test_no_acting_user_is_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cron / CLI runs carry no acting user, so the gate cannot apply."""
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.configurable_user_id",
        lambda: None,
    )
    monkeypatch.setattr(
        "octop.infra.agents.middleware.user_model_credentials.load_user_credentials",
        lambda *_args: pytest.fail("no acting user → no credential lookup"),
    )

    assert _run(_middleware(role="user"), _OpenAIModel()) == "ok"
