"""
MongoDB access: collections for users and lessons, and GridFS for files
(uploaded notes, finished videos, lesson records, fact reviews).

Set MONGO_URL=mongomock:// to use an in-memory database (test suite).
"""

import time
from datetime import datetime, timezone

import gridfs
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

from app.api.config import settings


def _make_client():
    if settings.mongo_url.startswith("mongomock://"):
        import mongomock
        import mongomock.gridfs

        mongomock.gridfs.enable_gridfs_integration()
        return mongomock.MongoClient(tz_aware=True)
    return MongoClient(settings.mongo_url, tz_aware=True, serverSelectionTimeoutMS=5000)


class Database:
    """Lazily connected handles, so importing the app never needs a server."""

    def __init__(self):
        self._client = None

    def connect(self):
        if self._client is None:
            self._client = _make_client()
        return self._client[settings.mongo_db]

    @property
    def users(self):
        return self.connect().users

    @property
    def lessons(self):
        return self.connect().lessons

    @property
    def files(self) -> gridfs.GridFS:
        return gridfs.GridFS(self.connect())

    def reset(self):
        """Drop everything (tests only)."""
        client = self._client or _make_client()
        client.drop_database(settings.mongo_db)
        self._client = None


db = Database()


def now() -> datetime:
    return datetime.now(timezone.utc)


def object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def init_db(attempts: int = 30, delay: float = 2.0) -> None:
    """Creates indexes; the server may still be starting (e.g. Docker)."""
    for attempt in range(attempts):
        try:
            db.users.create_index("email", unique=True)
            db.lessons.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
            db.lessons.create_index("status")
            return
        except PyMongoError as exc:
            if attempt == attempts - 1:
                raise
            print(f"[api] MongoDB not reachable yet ({exc.__class__.__name__}); retrying in {delay:.0f}s")
            time.sleep(delay)
