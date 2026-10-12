"""Apply a user's own model credentials to their model calls.

When the acting user has stored a ``(base_url, api_key)`` pair, every
OpenAI-compatible model call they make is re-pointed at their own endpoint so
the request is billed by their provider instead of the shared one.

A bring-your-own-key deployment can also *require* that pair
(``require_credential``): a non-admin user with no api_key of their own is
refused before the call is built, so a missing key fails loudly instead of
silently being billed to the shared provider.

The credential swap deliberately rebuilds the model with ``type(model)(**fields)``
rather than using ``model_copy(update=...)``: langchain populates the underlying
``root_client`` / ``client`` in its ``validate_environment`` validator, and
``model_copy`` copies those already-built clients verbatim — a copied model
would silently keep calling the shared endpoint. Re-instantiating runs the
validator again and builds a client from the overridden credentials.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from pydantic import SecretStr

from octop.infra.agents.middleware.token_quota import configurable_user_id
from octop.infra.agents.providers.user_credentials import load_user_credentials
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role

# Built by langchain's ``validate_environment`` and copied verbatim by
# ``model_copy``; skipped when re-deriving the model so they are rebuilt fresh.
_CLIENT_FIELDS = frozenset({"root_client", "client", "root_async_client", "async_client"})


def _credential_updates(credential: Any) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if credential.api_key:
        updates["openai_api_key"] = SecretStr(credential.api_key)
    if credential.base_url:
        updates["openai_api_base"] = credential.base_url
    return updates


def _with_credentials(model: Any, updates: dict[str, Any]) -> Any:
    """Return a new model instance, with a freshly built client, carrying ``updates``."""
    values: dict[str, Any] = {}
    for name in type(model).model_fields:
        if name in _CLIENT_FIELDS:
            continue
        values[name] = getattr(model, name)
    values.update(updates)
    return type(model)(**values)


class UserModelCredentialMiddleware(AgentMiddleware[Any, Any]):
    """Swap in the acting user's own credentials for OpenAI-compatible calls."""

    def __init__(
        self,
        *,
        credential_repo: Any,
        secret_repo: Any,
        require_credential: bool = False,
        user_repo: Any = None,
    ) -> None:
        super().__init__()
        self._credentials = credential_repo
        self._secrets = secret_repo
        self._require_credential = require_credential
        self._users = user_repo

    def _must_bring_own_key(self, user_id: int) -> bool:
        """True when *user_id* is a non-admin the policy refuses to serve."""
        if not self._require_credential or self._users is None:
            return False
        row = self._users.get(user_id)
        if row is None:
            return False
        # Admins keep using the shared provider: they configured it, and the
        # first-run wizard would otherwise dead-end before they can chat.
        return str(getattr(row, "role", "")) != Role.ADMIN

    def _assert_own_key(self, user_id: int) -> None:
        if self._must_bring_own_key(user_id):
            raise OctopError(
                ErrorCode.MODEL_CREDENTIAL_REQUIRED,
                "This account must configure its own model API key first.",
            )

    def _configured_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        user_id = configurable_user_id()
        if user_id is None:
            return request
        credential = load_user_credentials(self._credentials, self._secrets, user_id)
        # Checked before the protocol filter below: a shared-key Anthropic or
        # Google call is exactly what a bring-your-own-key deployment blocks.
        if credential is None or not credential.api_key:
            self._assert_own_key(user_id)
        if credential is None:
            return request
        model = request.model
        # Only OpenAI-compatible models expose these fields; other protocols
        # (Anthropic / Google) are left untouched rather than misconfigured.
        if not (hasattr(model, "openai_api_key") or hasattr(model, "openai_api_base")):
            return request
        updates = _credential_updates(credential)
        if not updates:
            return request
        return request.override(model=_with_credentials(model, updates))

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._configured_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._configured_request(request))


__all__ = ["UserModelCredentialMiddleware"]
