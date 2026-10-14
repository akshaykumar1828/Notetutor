"""Request and response bodies for the REST API."""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, PlainSerializer

try:  # EmailStr needs the optional email-validator package
    import email_validator  # noqa: F401
except ImportError:  # pragma: no cover
    EmailStr = str  # type: ignore[misc,assignment]


def _utc(value: datetime | None) -> str | None:
    """Stored as UTC; always sent with an explicit offset."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


UtcDateTime = Annotated[datetime, PlainSerializer(_utc)]


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: UtcDateTime

    @classmethod
    def from_doc(cls, doc: dict) -> "UserOut":
        return cls(id=str(doc["_id"]), email=doc["email"], name=doc["name"],
                   role=doc["role"], created_at=doc["created_at"])


class AuthOut(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


class LessonOut(BaseModel):
    id: str
    title: str
    source_kind: str
    status: str
    stage: str
    progress: int
    error: str | None
    duration_seconds: float | None
    scene_count: int | None
    options: dict
    created_at: UtcDateTime
    finished_at: UtcDateTime | None
    owner_id: str

    @classmethod
    def from_doc(cls, doc: dict) -> "LessonOut":
        return cls(
            id=str(doc["_id"]), title=doc["title"], source_kind=doc["source_kind"],
            status=doc["status"], stage=doc["stage"], progress=doc["progress"],
            error=doc.get("error"), duration_seconds=doc.get("duration_seconds"),
            scene_count=doc.get("scene_count"), options=doc.get("options", {}),
            created_at=doc["created_at"], finished_at=doc.get("finished_at"),
            owner_id=str(doc["owner_id"]),
        )


class LessonDetail(LessonOut):
    has_video: bool
    has_review: bool


class SceneScript(BaseModel):
    title: str
    visual: str
    narration: str


class RoleIn(BaseModel):
    role: str = Field(pattern="^(user|admin)$")
