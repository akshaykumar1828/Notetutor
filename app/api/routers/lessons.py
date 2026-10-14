"""Create, list, view, stream and delete lessons (files live in GridFS)."""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pymongo import DESCENDING

from app.api import worker
from app.api.config import settings
from app.api.db import db, now, object_id
from app.api.schemas import LessonDetail, LessonOut, SceneScript
from app.api.security import current_user

router = APIRouter(prefix="/api/lessons", tags=["lessons"])

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md"}
VOICES = {"af_heart", "af_bella", "am_michael", "bf_emma", "bm_george"}
CHUNK = 1024 * 1024


def _invalid(message: str, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> HTTPException:
    return HTTPException(code, message)


def _own_lesson(lesson_id: str, user: dict) -> dict:
    lesson = db.lessons.find_one({"_id": object_id(lesson_id)})
    if lesson is None or (lesson["owner_id"] != user["_id"] and user["role"] != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lesson not found")
    return lesson


def _check_upload(suffix: str, data: bytes) -> None:
    """Keeps uploads within what the free AI tiers can process."""
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise _invalid(f"File is larger than {settings.max_upload_mb} MB",
                       status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    if suffix == ".pdf":
        import pymupdf

        try:
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                pages = pdf.page_count
        except Exception:
            raise _invalid("This PDF could not be opened")
        if pages > settings.max_pdf_pages:
            raise _invalid(f"The PDF has {pages} pages; the limit is {settings.max_pdf_pages}. "
                           "Split it into smaller parts and make one lesson per part.")
    elif suffix in (".txt", ".md"):
        if len(data.decode("utf-8", errors="replace")) > settings.max_text_chars:
            raise _invalid(f"Text files are limited to {settings.max_text_chars:,} characters "
                           "(about 10 pages)")


@router.post("", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def create_lesson(
    file: UploadFile | None = File(default=None),
    topic: str | None = Form(default=None),
    voice: str = Form(default="af_heart"),
    captions: bool = Form(default=True),
    music: bool = Form(default=True),
    verify: bool = Form(default=True),
    user: dict = Depends(current_user),
):
    topic = (topic or "").strip()
    if bool(file) == bool(topic):
        raise _invalid("Provide either a file or a topic (not both)")
    if voice not in VOICES:
        raise _invalid("Unknown voice")

    lesson = {
        "owner_id": user["_id"], "status": "queued", "stage": "Waiting in queue", "progress": 0,
        "options": {"voice": voice, "captions": captions, "music": music, "verify": verify},
        "created_at": now(), "finished_at": None, "error": None,
    }
    if file:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise _invalid(f"Unsupported file type '{suffix}'")
        data = await file.read()
        _check_upload(suffix, data)
        lesson.update(
            source_kind="file", title=Path(file.filename).stem[:255],
            source_file_id=db.files.put(data, filename=f"notes{suffix}",
                                        contentType=file.content_type, owner_id=user["_id"]),
        )
    else:
        if len(topic) > 300:
            raise _invalid("Topic is too long")
        lesson.update(source_kind="topic", title=topic, topic=topic)

    lesson["_id"] = db.lessons.insert_one(lesson).inserted_id
    worker.submit(lesson["_id"])
    return LessonOut.from_doc(lesson)


@router.get("", response_model=list[LessonOut])
def list_lessons(user: dict = Depends(current_user)):
    cursor = db.lessons.find({"owner_id": user["_id"]}).sort("created_at", DESCENDING)
    return [LessonOut.from_doc(doc) for doc in cursor]


@router.get("/{lesson_id}", response_model=LessonDetail)
def get_lesson(lesson_id: str, user: dict = Depends(current_user)):
    lesson = _own_lesson(lesson_id, user)
    return LessonDetail(**LessonOut.from_doc(lesson).model_dump(),
                        has_video=bool(lesson.get("video_file_id")),
                        has_review=bool(lesson.get("review")))


@router.get("/{lesson_id}/video")
def get_video(lesson_id: str, download: bool = False, range: str | None = Header(default=None),
              user: dict = Depends(current_user)):
    lesson = _own_lesson(lesson_id, user)
    if not lesson.get("video_file_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not ready")
    video = db.files.get(lesson["video_file_id"])
    size = video.length

    # Range requests let the browser player seek without downloading it all.
    start, end, code = 0, size - 1, status.HTTP_200_OK
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (range or "").strip())
    if match and (match[1] or match[2]):
        if match[1]:
            start = int(match[1])
            end = min(int(match[2]), size - 1) if match[2] else size - 1
        else:  # suffix range: last N bytes
            start = max(size - int(match[2]), 0)
        if start > end:
            raise HTTPException(status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                                headers={"Content-Range": f"bytes */{size}"})
        code = status.HTTP_206_PARTIAL_CONTENT

    def body():
        video.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = video.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    name = re.sub(r'[^\w\- ]', '', lesson["title"])[:60] or "lesson"
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if code == status.HTTP_206_PARTIAL_CONTENT:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}.mp4"'
    return StreamingResponse(body(), status_code=code, media_type="video/mp4", headers=headers)


@router.get("/{lesson_id}/script", response_model=list[SceneScript])
def get_script(lesson_id: str, user: dict = Depends(current_user)):
    lesson = _own_lesson(lesson_id, user)
    if not lesson.get("script"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not ready")
    return lesson["script"]


@router.get("/{lesson_id}/review", response_class=PlainTextResponse)
def get_review(lesson_id: str, user: dict = Depends(current_user)):
    lesson = _own_lesson(lesson_id, user)
    if not lesson.get("review"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review not ready")
    return lesson["review"]


@router.delete("/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lesson(lesson_id: str, user: dict = Depends(current_user)):
    lesson = _own_lesson(lesson_id, user)
    if lesson["status"] in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait until the lesson has finished")
    for key in ("source_file_id", "video_file_id", "record_file_id"):
        if lesson.get(key):
            db.files.delete(lesson[key])
    db.lessons.delete_one({"_id": lesson["_id"]})
