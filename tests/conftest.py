import os

# Tests use a throwaway in-memory MongoDB instead of a real server.
os.environ.setdefault("MONGO_URL", "mongomock://")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
