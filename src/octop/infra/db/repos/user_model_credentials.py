"""Per-user model credentials — bring-your-own provider key."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, now_ts


@dataclass(frozen=True)
class UserModelCredentialRow:
    user_id: int
    base_url: str | None
    credential_blob: bytes | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, r: DbRow) -> UserModelCredentialRow:
        raw_base = r["base_url"]
        blob = r["credential_blob"]
        return cls(
            user_id=int(r["user_id"]),
            base_url=(str(raw_base).strip() or None) if raw_base is not None else None,
            credential_blob=bytes(blob) if blob is not None else None,
            created_at=int(r["created_at"]),
            updated_at=int(r["updated_at"]),
        )


class UserModelCredentialRepo:
    """One optional row per user; absence means "use the shared providers"."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, user_id: int) -> UserModelCredentialRow | None:
        with self._db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM user_model_credentials WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return UserModelCredentialRow.from_row(r) if r else None

    def upsert(
        self,
        user_id: int,
        *,
        base_url: str | None,
        credential_blob: bytes | None,
    ) -> UserModelCredentialRow:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO user_model_credentials("
                "user_id, base_url, credential_blob, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "base_url = excluded.base_url, "
                "credential_blob = excluded.credential_blob, "
                "updated_at = excluded.updated_at",
                (user_id, base_url, credential_blob, ts, ts),
            )
        row = self.get(user_id)
        assert row is not None  # noqa: S101
        return row

    def delete(self, user_id: int) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM user_model_credentials WHERE user_id = ?",
                (user_id,),
            )
            return int(getattr(cur, "rowcount", 0) or 0) > 0
