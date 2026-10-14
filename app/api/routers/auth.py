"""Registration, login (JWT in an httpOnly cookie + returned token), logout."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pymongo.errors import DuplicateKeyError

from app.api.config import settings
from app.api.db import db, now
from app.api.schemas import AuthOut, LoginIn, RegisterIn, UserOut
from app.api.security import (
    COOKIE_NAME, create_token, current_user, hash_password, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _start_session(response: Response, user: dict) -> AuthOut:
    token = create_token(user)
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
        max_age=settings.jwt_minutes * 60, path="/",
    )
    return AuthOut(user=UserOut.from_doc(user), access_token=token)


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, response: Response):
    email = body.email.lower().strip()
    is_first_user = db.users.estimated_document_count() == 0
    role = "admin" if is_first_user or (settings.admin_email and email == settings.admin_email) else "user"
    user = {"email": email, "name": body.name.strip(), "role": role,
            "password_hash": hash_password(body.password), "created_at": now()}
    try:
        user["_id"] = db.users.insert_one(user).inserted_id
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    return _start_session(response, user)


@router.post("/login", response_model=AuthOut)
def login(body: LoginIn, response: Response):
    user = db.users.find_one({"email": body.email.lower().strip()})
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong e-mail or password")
    return _start_session(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(current_user)):
    return UserOut.from_doc(user)
