"""REST API tests (in-memory MongoDB): auth, RBAC, upload limits,
GridFS storage, video range requests, lesson lifecycle."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api import worker
from app.api.db import db, init_db, object_id
from app.api.main import app


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    db.reset()
    init_db()
    submitted = []
    monkeypatch.setattr(worker, "submit", submitted.append)
    yield submitted


def register(client, email="ada@example.com", name="Ada", password="password123"):
    return client.post("/api/auth/register", json={"email": email, "name": name, "password": password})


def make_pdf(pages: int) -> bytes:
    import pymupdf

    with pymupdf.open() as pdf:
        for i in range(pages):
            pdf.new_page().insert_text((72, 72), f"Page {i + 1}")
        return pdf.tobytes()


def finish(lesson_id: str, tmp_path, video: bytes = b"0123456789" * 100):
    """Simulates the worker finishing: outputs are stored in MongoDB."""
    out = tmp_path / "lesson.mp4"
    out.write_bytes(video)
    out.with_suffix(".json").write_text(json.dumps({"scenes": [
        {"title": "Intro", "visual": {"visual_type": "key_points"},
         "sentences": ["Hi.", "Bye."], "audio_seconds": 3.0}]}), encoding="utf-8")
    out.with_suffix(".review.md").write_text("# Review\n0 claims contradict.", encoding="utf-8")
    worker._store_results(object_id(lesson_id), out)


def test_register_login_me_and_logout():
    client = TestClient(app)
    r = register(client)
    assert r.status_code == 201 and r.json()["user"]["role"] == "admin"  # first user
    assert r.json()["user"]["created_at"].endswith("+00:00")
    assert client.get("/api/auth/me").json()["email"] == "ada@example.com"  # via cookie

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    token = client.post("/api/auth/login",
                        json={"email": "ADA@example.com", "password": "password123"}).json()["access_token"]
    other = TestClient(app)
    assert other.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_bad_credentials_and_duplicates():
    client = TestClient(app)
    register(client)
    assert register(client).status_code == 409
    assert client.post("/api/auth/login", json={"email": "ada@example.com", "password": "nope"}).status_code == 401
    assert register(TestClient(app), email="x@example.com", password="short").status_code == 422


def test_role_based_access():
    admin = TestClient(app)
    register(admin)
    user = TestClient(app)
    register(user, email="bob@example.com", name="Bob")
    assert user.get("/api/admin/users").status_code == 403
    users = admin.get("/api/admin/users").json()
    assert [u["role"] for u in users] == ["admin", "user"]
    assert admin.patch(f"/api/admin/users/{users[1]['id']}/role", json={"role": "admin"}).json()["role"] == "admin"
    assert admin.patch(f"/api/admin/users/{users[0]['id']}/role", json={"role": "user"}).status_code == 409


def test_upload_limits():
    client = TestClient(app)
    register(client)
    ok = client.post("/api/lessons", files={"file": ("ok.pdf", make_pdf(10), "application/pdf")})
    assert ok.status_code == 201
    too_long = client.post("/api/lessons", files={"file": ("long.pdf", make_pdf(11), "application/pdf")})
    assert too_long.status_code == 422 and "11 pages" in too_long.json()["detail"]
    text = client.post("/api/lessons", files={"file": ("big.txt", b"a" * 40_001, "text/plain")})
    assert text.status_code == 422
    bad = client.post("/api/lessons", files={"file": ("virus.exe", b"x", "application/octet-stream")})
    assert bad.status_code == 422


def test_lesson_lifecycle_with_gridfs(fresh_db, tmp_path):
    client = TestClient(app)
    register(client)
    assert client.post("/api/lessons", data={"topic": ""}).status_code == 422
    r = client.post("/api/lessons", data={"topic": "How DNS works", "voice": "am_michael", "music": "false"})
    assert r.status_code == 201
    lesson = r.json()
    assert lesson["status"] == "queued" and lesson["options"]["music"] is False
    assert [str(i) for i in fresh_db] == [lesson["id"]]  # handed to the worker

    upload = client.post("/api/lessons", files={"file": ("notes.png", b"\x89PNG data", "image/png")})
    assert upload.status_code == 201 and upload.json()["title"] == "notes"
    stored = db.lessons.find_one({"_id": object_id(upload.json()["id"])})
    assert db.files.get(stored["source_file_id"]).read() == b"\x89PNG data"  # notes in GridFS

    assert len(client.get("/api/lessons").json()) == 2
    assert client.get(f"/api/lessons/{lesson['id']}/video").status_code == 404  # not ready

    finish(lesson["id"], tmp_path)
    detail = client.get(f"/api/lessons/{lesson['id']}").json()
    assert detail["status"] == "done" and detail["has_video"] and detail["has_review"]
    assert detail["scene_count"] == 1 and detail["duration_seconds"] == 3.0
    assert client.get(f"/api/lessons/{lesson['id']}/script").json()[0]["narration"] == "Hi. Bye."
    assert "contradict" in client.get(f"/api/lessons/{lesson['id']}/review").text

    full = client.get(f"/api/lessons/{lesson['id']}/video")
    assert full.status_code == 200 and len(full.content) == 1000
    part = client.get(f"/api/lessons/{lesson['id']}/video", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == b"0123456789"
    assert part.headers["content-range"] == "bytes 10-19/1000"
    tail = client.get(f"/api/lessons/{lesson['id']}/video", headers={"Range": "bytes=-5"})
    assert tail.content == b"56789"

    files_before = db.connect().fs.files.count_documents({})
    assert client.delete(f"/api/lessons/{lesson['id']}").status_code == 204
    assert db.connect().fs.files.count_documents({}) == files_before - 2  # video + record removed
    assert client.delete(f"/api/lessons/{upload.json()['id']}").status_code == 409  # still queued


def test_users_cannot_see_each_others_lessons():
    alice = TestClient(app)
    register(alice)
    lesson_id = alice.post("/api/lessons", data={"topic": "Photosynthesis"}).json()["id"]
    bob = TestClient(app)
    register(bob, email="bob@example.com")
    assert bob.get(f"/api/lessons/{lesson_id}").status_code == 404
    assert bob.get("/api/lessons/not-an-id").status_code == 404
    assert bob.get("/api/lessons").json() == []
    assert alice.get("/api/admin/lessons").status_code == 200  # alice is admin


def test_worker_progress_parsing():
    client = TestClient(app)
    register(client)
    lesson_id = object_id(client.post("/api/lessons", data={"topic": "Gravity"}).json()["id"])
    worker._track_progress(lesson_id, "[4] Planning the lesson...")
    worker._track_progress(lesson_id, "=== Scene 2/4: Newton's laws (conceptual_explanation)")
    lesson = db.lessons.find_one({"_id": lesson_id})
    assert lesson["progress"] == 25 + 15 and "scene 2 of 4" in lesson["stage"]


def test_worker_runs_in_temp_dir_and_cleans_up(monkeypatch, tmp_path):
    """The pipeline runs in a temporary folder that is removed afterwards."""
    client = TestClient(app)
    register(client)
    lesson_id = object_id(client.post(
        "/api/lessons", files={"file": ("n.txt", b"Some notes", "text/plain")}).json()["id"])
    seen = {}

    class FakeProcess:
        def __init__(self, command, cwd, env, **_):
            work = env["NOTETUTOR_WORK_DIR"]
            seen.update(work=work, source=open(command[4], "rb").read())
            out = __import__("pathlib").Path(command[6])
            out.write_bytes(b"video")
            out.with_suffix(".json").write_text('{"scenes": []}', encoding="utf-8")
            self.stdout, self.returncode = iter(["[4] Planning the lesson...\n"]), 0

        def wait(self):
            return 0

    monkeypatch.setattr(worker.subprocess, "Popen", FakeProcess)
    worker._run(lesson_id)
    lesson = db.lessons.find_one({"_id": lesson_id})
    assert lesson["status"] == "done" and db.files.get(lesson["video_file_id"]).read() == b"video"
    assert seen["source"] == b"Some notes"
    assert not __import__("os").path.exists(seen["work"])  # temporary folder removed


def test_command_reflects_options(tmp_path):
    lesson = {"options": {"captions": False, "music": True, "verify": False}}
    command = worker.build_command(lesson, "topic text", tmp_path / "out.mp4")
    assert "--no-captions" in command and "--no-verify" in command and "--no-music" not in command
