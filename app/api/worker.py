"""
Background lesson generation.

Lessons run one at a time (CPU-heavy rendering and the free AI quota both
make parallel runs counter-productive). Each run:

1. copies the uploaded notes from MongoDB (GridFS) into a temporary folder,
2. runs the normal command-line pipeline there, with all working files
   (voice clips, page images, music) in that folder,
3. stores the video, the lesson record and the fact review back in GridFS,
4. deletes the temporary folder, so nothing is left on disk.

The pipeline's log is parsed into a stage and a progress percentage that
the web app polls.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.api.config import ROOT
from app.api.db import db, now

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lesson")
_lock = threading.Lock()

# Log line prefix -> (stage shown to the user, progress %)
STAGES = (
    ("[1] Reading", "Reading your material", 5),
    ("[2] Extracting", "Understanding its structure", 12),
    ("[3] Checking facts", "Checking facts", 18),
    ("[4] Planning", "Planning the lesson", 25),
    ("[5] Rendering", "Rendering the video", 85),
    ("[pipeline] Review", "Writing the fact review", 97),
)
SCENE = re.compile(r"=== Scene (\d+)/(\d+): (.*?) \(")


def submit(lesson_id) -> None:
    _pool.submit(_run, lesson_id)


def _update(lesson_id, **fields) -> None:
    with _lock:
        db.lessons.update_one({"_id": lesson_id}, {"$set": fields})


def build_command(lesson: dict, source: str, output: Path) -> list[str]:
    options = lesson.get("options") or {}
    command = [sys.executable, "-u", "-m", "app.pipeline.lesson_video", source, "-o", str(output)]
    for flag, key in (("--no-captions", "captions"), ("--no-music", "music"),
                      ("--no-verify", "verify")):
        if options.get(key) is False:
            command.append(flag)
    return command


def _run(lesson_id) -> None:
    lesson = db.lessons.find_one({"_id": lesson_id})
    if lesson is None:
        return
    _update(lesson_id, status="running", stage="Starting", progress=2)

    with tempfile.TemporaryDirectory(prefix="notetutor_") as tmp:
        work = Path(tmp)
        if lesson["source_kind"] == "file":
            stored = db.files.get(lesson["source_file_id"])
            source = str(work / f"notes{Path(stored.filename).suffix}")
            Path(source).write_bytes(stored.read())
        else:
            source = lesson["topic"]
        output = work / "lesson.mp4"

        env = dict(os.environ, PYTHONIOENCODING="utf-8", NOTETUTOR_WORK_DIR=str(work))
        voice = (lesson.get("options") or {}).get("voice")
        if voice:
            env["NOTETUTOR_TTS_VOICE"] = voice

        tail: list[str] = []
        try:
            process = subprocess.Popen(
                build_command(lesson, source, output), cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in process.stdout:
                line = line.rstrip()
                tail = (tail + [line])[-40:]
                _track_progress(lesson_id, line)
            process.wait()
        except Exception as exc:  # the pipeline could not even start
            _update(lesson_id, status="failed", stage="Failed", error=str(exc)[:2000],
                    finished_at=now())
            return

        if process.returncode == 0 and output.exists():
            _store_results(lesson_id, output)
        else:
            reason = next((l for l in reversed(tail) if "Error" in l or "unavailable" in l.lower()),
                          tail[-1] if tail else "Unknown error")
            _update(lesson_id, status="failed", stage="Failed", error=reason[-2000:],
                    finished_at=now())


def _store_results(lesson_id, video: Path) -> None:
    """Moves the finished outputs from the temporary folder into MongoDB."""
    record_path, review_path = video.with_suffix(".json"), video.with_suffix(".review.md")
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    scenes = record.get("scenes", [])
    files = db.files
    fields = {
        "status": "done", "stage": "Ready", "progress": 100, "finished_at": now(),
        "video_file_id": files.put(video.read_bytes(), filename=f"lesson_{lesson_id}.mp4",
                                   contentType="video/mp4", lesson_id=lesson_id),
        "duration_seconds": sum(s["audio_seconds"] for s in scenes) or None,
        "scene_count": len(scenes) or None,
        "script": [{"title": s["title"], "visual": s["visual"]["visual_type"],
                    "narration": " ".join(s["sentences"])} for s in scenes],
    }
    if record_path.exists():
        fields["record_file_id"] = files.put(record_path.read_bytes(), filename=record_path.name,
                                             contentType="application/json", lesson_id=lesson_id)
    if review_path.exists():
        fields["review"] = review_path.read_text(encoding="utf-8")
    _update(lesson_id, **fields)


def _track_progress(lesson_id, line: str) -> None:
    stripped = line.strip()
    match = SCENE.search(stripped)
    if match:
        done, total, title = int(match[1]) - 1, int(match[2]), match[3]
        _update(lesson_id, stage=f"Writing scene {match[1]} of {total}: {title}",
                progress=25 + int(60 * done / max(total, 1)))
        return
    for prefix, stage, progress in STAGES:
        if stripped.startswith(prefix):
            _update(lesson_id, stage=stage, progress=progress)
            return


def recover_interrupted() -> None:
    """Lessons left 'running' or 'queued' by a previous server process."""
    db.lessons.update_many(
        {"status": "running"},
        {"$set": {"status": "failed", "stage": "Failed", "finished_at": now(),
                  "error": "The server restarted while this lesson was being generated."}},
    )
    for lesson in db.lessons.find({"status": "queued"}, {"_id": 1}):
        submit(lesson["_id"])
