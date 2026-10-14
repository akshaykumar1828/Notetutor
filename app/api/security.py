"""
Password hashing (bcrypt), JWT tokens, and request authentication.

A token is accepted from an httpOnly session cookie (used by the web app,
not readable by JavaScript) or an "Authorization: Bearer" header (for API
clients). Role-based access is enforced with require_role().
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Cookie, Depends, Header, HTTPException, status

from app.api.config import settings
from app.api.db import db, object_id

COOKIE_NAME = "notetutor_session"
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["_id"]),
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail,
                         headers={"WWW-Authenticate": "Bearer"})


def current_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict:
    token = session
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not token:
        raise _unauthorized()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token")
    user = db.users.find_one({"_id": object_id(payload["sub"])})
    if user is None:
        raise _unauthorized("Account no longer exists")
    return user


def require_role(*roles: str):
    def check(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for your role")
        return user

    return check
