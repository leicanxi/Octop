"""Per-user model credentials — encrypted storage and lookup.

A user may store one ``(base_url, api_key)`` pair of their own. When present it
is applied at model-call time (see ``middleware/user_model_credentials.py``) so
the request is billed by the user's own provider instead of the shared one.
"""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.user_model_credentials import UserModelCredentialRepo

_API_KEY_FIELD = "api_key"


@dataclass(frozen=True)
class UserModelCredential:
    base_url: str | None
    api_key: str | None


def store_user_credentials(
    credential_repo: UserModelCredentialRepo,
    secret_repo: SecretRepo,
    *,
    user_id: int,
    base_url: str | None,
    api_key: str | None,
) -> None:
    """Insert or replace a user's credential; empty fields clear them."""
    clean_base = (base_url or "").strip() or None
    clean_key = (api_key or "").strip()
    if not clean_base and not clean_key:
        credential_repo.delete(user_id)
        return
    payload = {_API_KEY_FIELD: clean_key}
    credential_repo.upsert(
        user_id,
        base_url=clean_base,
        credential_blob=encrypt_credentials(secret_repo, payload),
    )


def load_user_credentials(
    credential_repo: UserModelCredentialRepo,
    secret_repo: SecretRepo,
    user_id: int,
) -> UserModelCredential | None:
    """Return the user's credential, or ``None`` when they use the shared ones."""
    row = credential_repo.get(user_id)
    if row is None:
        return None
    api_key: str | None = None
    if row.credential_blob:
        raw = decrypt_credentials(secret_repo, row.credential_blob).get(_API_KEY_FIELD)
        if raw is not None:
            api_key = str(raw).strip() or None
    if not row.base_url and not api_key:
        return None
    return UserModelCredential(base_url=row.base_url, api_key=api_key)


def plugin_credential_payload(
    credential_repo: UserModelCredentialRepo,
    secret_repo: SecretRepo,
    user_id: int,
) -> dict[str, str] | None:
    """Return the acting user's credential for plugin tools (never shared keys).

    Plugins that call a user-billed endpoint (for example the ``sing-song``
    model on new-api) read this from the run's internal tool context. It is
    intentionally unavailable when the user has no API key so a missing key
    fails loudly instead of charging the operator's shared account.
    """
    credential = load_user_credentials(credential_repo, secret_repo, user_id)
    if credential is None or not credential.api_key:
        return None
    return {"base_url": credential.base_url or "", "api_key": credential.api_key}
