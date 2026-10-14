"""Admin-only endpoints (role-based access)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.api.db import db, object_id
from app.api.schemas import LessonOut, RoleIn, UserOut
from app.api.security import require_role

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_only = require_role("admin")


@router.get("/users", response_model=list[UserOut])
def list_users(_: dict = Depends(admin_only)):
    return [UserOut.from_doc(u) for u in db.users.find().sort("created_at", ASCENDING)]


@router.patch("/users/{user_id}/role", response_model=UserOut)
def set_role(user_id: str, body: RoleIn, admin: dict = Depends(admin_only)):
    target = object_id(user_id)
    if target == admin["_id"] and body.role != "admin":
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot remove your own admin role")
    user = db.users.find_one_and_update({"_id": target}, {"$set": {"role": body.role}},
                                        return_document=ReturnDocument.AFTER)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return UserOut.from_doc(user)


@router.get("/lessons", response_model=list[LessonOut])
def all_lessons(_: dict = Depends(admin_only)):
    return [LessonOut.from_doc(d) for d in db.lessons.find().sort("created_at", DESCENDING)]
