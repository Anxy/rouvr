"""Local session auth (Step 1): FastAPI Users + argon2, cookie transport.

Stock FastAPI Users 15 building blocks (UserManager, SQLAlchemy user
database, CookieTransport, JWTStrategy, pwdlib argon2 helper). Only custom
login/logout routes are mounted — no register/users/verify/reset routers,
so tenant/role are never user-settable. Sessions coexist with the existing
token auth; cookie sessions additionally pass a CSRF check on
state-changing requests. Login is per-IP rate limited.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users.schemas import BaseUserCreate
from fastapi_users.authentication import CookieTransport, JWTStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = logging.getLogger(__name__)

COOKIE_NAME = "rouvr_session"
SESSION_LIFETIME_S = 12 * 3600
# Industry-standard persistent login for private devices (remember-me):
# 30 days, issued only when the login form explicitly opts in.
REMEMBER_LIFETIME_S = 30 * 24 * 3600
ADMIN_USERNAME = "admin"
EMAIL_DOMAIN = "local"
ADMIN_TENANT = "demo"
ADMIN_ROLE = "owner"


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    tenant: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")

    @property
    def username(self) -> str:
        return self.email.split("@")[0]


def argon2_helper() -> PasswordHash:
    return PasswordHash((Argon2Hasher(),))


class UserCreate(BaseUserCreate):
    # Plain str (not EmailStr): local-only identities like admin@local use a
    # reserved suffix that validators reject. Never user-settable: no
    # register router is mounted, so this schema only serves the seed path.
    email: str
    tenant: str = ADMIN_TENANT
    role: str = "member"


class UserManager(UUIDIDMixin, BaseUserManager["User", uuid.UUID]):
    async def validate_password(self, password: str, user) -> None:
        if len(password) < 12:
            from fastapi_users.exceptions import InvalidPasswordException

            raise InvalidPasswordException(
                reason="Password must be at least 12 characters long."
            )

    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> Optional["User"]:
        name = (credentials.username or "").strip()
        if "@" not in name:
            credentials.username = f"{name}@{EMAIL_DOMAIN}"
        return await super().authenticate(credentials)


def make_manager(session: AsyncSession) -> UserManager:
    from fastapi_users.password import PasswordHelper

    return UserManager(SQLAlchemyUserDatabase(session, User), PasswordHelper(argon2_helper()))


def cookie_secure_for_host(host: str) -> bool:
    """Secure cookies everywhere except loopback binds (plain-http local dev)."""
    return host not in ("127.0.0.1", "localhost", "::1")


def csrf_token_for(user_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()


def csrf_valid(provided: str, user_id: str, secret: str) -> bool:
    return hmac.compare_digest(provided or "", csrf_token_for(user_id, secret))


def emit_secret_once(secret: str, stream=None) -> bool:
    """Print a generated secret exactly once to a terminal, never to logs.

    Writes only to an explicit stream or /dev/tty. Returns True when shown.
    """
    text = f"Rouvr admin password (printed once, not logged): {secret}\n"
    if stream is not None:
        stream.write(text)
        stream.flush()
        return True
    try:
        fd = os.open("/dev/tty", os.O_WRONLY)
    except OSError:
        log.info("admin password generated; no terminal attached: set ROUVR_ADMIN_PASSWORD and restart to use a known password")
        return False
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return True


class LoginThrottle:
    """Simple per-IP backoff after 5 failed logins. 429 + Retry-After."""

    def __init__(self, max_entries: int = 10000) -> None:
        self._fails: dict[str, list] = {}
        self._max = max_entries

    def check(self, ip: str, now: Optional[float] = None) -> int:
        """Return 0 when allowed, else seconds until retry."""
        now = time.time() if now is None else now
        rec = self._fails.get(ip)
        if not rec:
            return 0
        fails, blocked_until = rec
        if fails <= 5 or now >= blocked_until:
            return 0
        return int(blocked_until - now) + 1

    def record(self, ip: str, ok: bool, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if ok:
            self._fails.pop(ip, None)
            return
        fails, _ = self._fails.get(ip, (0, 0.0))
        fails += 1
        if len(self._fails) >= self._max and ip not in self._fails:
            self._fails.pop(next(iter(self._fails)))
        if fails > 5:
            wait = min(300, 15 * 2 ** (fails - 5))
            self._fails[ip] = (fails, now + wait)
        else:
            self._fails[ip] = (fails, 0.0)


def sqlite_async_url(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + db_url[len("sqlite:///"):]
    raise ValueError(f"auth supports sqlite URLs only, got {db_url!r}")


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT = {"/auth/login", "/api/v1/health", "/health"}


def csrf_allows(request: Request, session_user_id: str, secret: str) -> bool:
    """Origin/Referer host match, else double-submit token. Login is exempt."""
    if request.method in SAFE_METHODS:
        return True
    if request.url.path in CSRF_EXEMPT:
        return True
    host = (request.headers.get("host") or "").split(":")[0].lower()
    for hdr in ("origin", "referer"):
        val = request.headers.get(hdr)
        if val:
            from urllib.parse import urlparse

            if (urlparse(val).hostname or "").lower() == host:
                return True
            return False
    return csrf_valid(request.headers.get("x-csrf-token", ""), session_user_id, secret)


def seed_admin(admin_password: Optional[str], sync_engine) -> tuple[str, bool]:
    """Ensure the owner exists, using only the auth `user` table.

    Fully synchronous so ``make_app`` stays callable from async tests (no
    ``asyncio.run``). No ledger side effects: no tenant/project rows, no
    ``users`` row — cookie identity carries tenant/role from the user row
    itself, so existing enumeration tests are unaffected. Returns
    (password_or_empty, generated). Provided passwords honour the same
    minimum length as interactive registration.
    """
    import uuid as _uuid_mod

    generated = False
    if not admin_password:
        admin_password = secrets.token_urlsafe(24)
        generated = True
    elif len(admin_password) < 12:
        raise ValueError("ROUVR_ADMIN_PASSWORD must be at least 12 characters")

    email = f"{ADMIN_USERNAME}@{EMAIL_DOMAIN}"
    Base.metadata.create_all(sync_engine, tables=[User.__table__])
    cols = User.__table__.c
    with sync_engine.begin() as conn:
        exists = conn.execute(
            User.__table__.select().where(cols.email == email).limit(1)).first()
        if exists is None:
            conn.execute(User.__table__.insert().values(
                id=_uuid_mod.uuid4(), email=email,
                hashed_password=argon2_helper().hash(admin_password),
                is_active=True, is_verified=True, is_superuser=False,
                tenant=ADMIN_TENANT, role=ADMIN_ROLE))
    return admin_password, generated


def build_auth_router(
    *,
    secret: str,
    cookie_secure: bool,
    get_session,
) -> tuple[APIRouter, CookieTransport, JWTStrategy, LoginThrottle]:
    transport = CookieTransport(
        cookie_name=COOKIE_NAME,
        cookie_max_age=SESSION_LIFETIME_S,
        cookie_secure=cookie_secure,
        cookie_httponly=True,
        cookie_samesite="lax",
    )

    def get_strategy() -> JWTStrategy:
        return JWTStrategy(secret=secret, lifetime_seconds=SESSION_LIFETIME_S)

    throttle = LoginThrottle()
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post("/login")
    async def login(request: Request, credentials: OAuth2PasswordRequestForm = Depends()):
        ip = request.client.host if request.client else "unknown"
        wait = throttle.check(ip)
        if wait:
            return JSONResponse({"detail": "too many login attempts"}, status_code=429,
                                headers={"Retry-After": str(wait)})
        async with get_session() as session:
            user = await make_manager(session).authenticate(credentials)
        if user is None or not user.is_active:
            throttle.record(ip, False)
            wait = throttle.check(ip)
            if wait:
                return JSONResponse({"detail": "too many login attempts"}, status_code=429,
                                    headers={"Retry-After": str(wait)})
            return JSONResponse({"detail": "invalid username or password"}, status_code=401)
        throttle.record(ip, True)
        try:
            form = await request.form()
            remember = str(form.get("remember", "")).lower() in ("1", "true", "on", "yes")
        except Exception:
            remember = False
        lifetime = REMEMBER_LIFETIME_S if remember else SESSION_LIFETIME_S
        token = await JWTStrategy(secret=secret, lifetime_seconds=lifetime).write_token(user)
        body = {
            "id": str(user.id),
            "username": user.email.split("@")[0],
            "tenant": user.tenant,
            "role": user.role,
            "csrf_token": csrf_token_for(str(user.id), secret),
        }
        resp = JSONResponse(body)
        resp.set_cookie(
            transport.cookie_name, token,
            max_age=lifetime, path=transport.cookie_path,
            secure=transport.cookie_secure, httponly=transport.cookie_httponly,
            samesite=transport.cookie_samesite,
        )
        return resp

    @router.post("/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(transport.cookie_name, path=transport.cookie_path)
        return resp

    return router, transport, get_strategy(), throttle
