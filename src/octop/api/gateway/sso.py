"""Session SSO for the sing gateway's voice plaza.

FlowBee (new-api) signs a short-lived identity ticket after the user logs in on
its own site (``/api/user/octop_sso`` -> ``OCTOP_SSO_SECRET``, HS256, 5 min,
claims ``sub``/``username``/``name``/``email``). This module verifies that
ticket, mints our own longer-lived session JWT (``SING_JWT_SECRET``) and owns
the cookie/CSRF helpers used by the auth endpoints.

All secrets live in env vars; the gateway keeps no user database.
"""

from __future__ import annotations

import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException, Request, Response

SESSION_COOKIE = "sing_session"
SSO_COOKIE = "sing_sso"
SSO_COOKIE_PATH = "/v1/auth"
SSO_TTL_SECONDS = 600
CLOCK_SKEW_SECONDS = 60
DEFAULT_NEXT = "/account.html"
ALLOWED_NEXT = ("/", "/index.html", "/upload.html", "/account.html")
# Cookie values must stay inside RFC6265 cookie-octets (no '/', no quotes), so
# the redirect target travels as a short key.
_NEXT_KEYS = {
    "/": "home",
    "/index.html": "home",
    "/upload.html": "upload",
    "/account.html": "account",
}
_NEXT_PATHS = {key: path for path, key in _NEXT_KEYS.items()}
_NEXT_PATHS["home"] = "/"


@dataclass(frozen=True)
class Identity:
    uid: int
    username: str
    name: str


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def jwt_secret() -> str:
    return _env("SING_JWT_SECRET")


def sso_secret() -> str:
    return _env("SING_SSO_SECRET")


def sso_url() -> str:
    return _env("SING_SSO_URL", "https://flowbee.top/sso/octop")


def plaza_base() -> str:
    return _env("SING_PLAZA_BASE", "https://voice-models.flowbee.top").rstrip("/")


def session_ttl_seconds() -> int:
    try:
        hours = int(_env("SING_JWT_TTL_HOURS", "168"))
    except ValueError:
        hours = 168
    return max(1, hours) * 3600


def normalize_next(raw: str | None) -> str:
    value = (raw or "").strip()
    return value if value in ALLOWED_NEXT else DEFAULT_NEXT


def new_state() -> str:
    return secrets.token_urlsafe(32)


def encode_next(next_path: str) -> str:
    return _NEXT_KEYS.get(next_path, "account")


def decode_next(key: str) -> str:
    return _NEXT_PATHS.get(key, DEFAULT_NEXT)


# ---- cookie helpers --------------------------------------------------------


def set_sso_cookie(response: Response, state: str, next_path: str) -> None:
    response.set_cookie(
        SSO_COOKIE,
        f"{state}.{encode_next(next_path)}",
        max_age=SSO_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path=SSO_COOKIE_PATH,
    )


def read_sso_cookie(request: Request) -> tuple[str, str] | None:
    raw = request.cookies.get(SSO_COOKIE) or ""
    state, _, key = raw.partition(".")
    if not state:
        return None
    return state, decode_next(key)


def clear_sso_cookie(response: Response) -> None:
    response.delete_cookie(SSO_COOKIE, path=SSO_COOKIE_PATH)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=session_ttl_seconds(),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# ---- token handling --------------------------------------------------------


def verify_sso_ticket(code: str) -> Identity:
    secret = sso_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="登录服务未配置")
    if not code:
        raise HTTPException(status_code=401, detail="缺少登录凭据")
    try:
        payload = jwt.decode(
            code,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
            leeway=CLOCK_SKEW_SECONDS,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="登录凭据已过期，请重试") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="登录凭据无效") from exc
    return _identity_from_payload(payload)


def issue_session(identity: Identity) -> str:
    secret = jwt_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="登录服务未配置")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(identity.uid),
            "username": identity.username,
            "name": identity.name,
            "iat": now,
            "exp": now + session_ttl_seconds(),
        },
        secret,
        algorithm="HS256",
    )


def _identity_from_payload(payload: dict[str, Any]) -> Identity:
    raw_uid = str(payload.get("sub") or "").strip()
    username = str(payload.get("username") or "").strip()
    if not raw_uid.isdigit() or int(raw_uid) <= 0 or not username:
        raise HTTPException(status_code=401, detail="登录凭据缺少身份信息")
    name = str(payload.get("name") or "").strip() or username
    return Identity(uid=int(raw_uid), username=username, name=name)


def require_identity(request: Request) -> Identity:
    """FastAPI dependency: resolve the caller from the session cookie/bearer."""
    secret = jwt_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="登录服务未配置")
    token = request.cookies.get(SESSION_COOKIE) or ""
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
            leeway=CLOCK_SKEW_SECONDS,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="登录凭据无效") from exc
    return _identity_from_payload(payload)


# ---- CSRF fallback ---------------------------------------------------------


def check_origin(request: Request) -> None:
    """Reject cross-site state changes (SameSite=Lax is the primary defence)."""
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origin:
        return  # non-browser clients (curl/tests) send no Origin
    host = urllib.parse.urlparse(origin).netloc.lower()
    allowed = {urllib.parse.urlparse(plaza_base()).netloc.lower()}
    if host in allowed or host.startswith(("127.0.0.1", "localhost")):
        return
    raise HTTPException(status_code=403, detail="非法请求来源")
