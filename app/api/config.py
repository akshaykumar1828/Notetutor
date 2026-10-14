"""Backend settings, read from environment variables (or .env)."""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    mongo_url: str = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    mongo_db: str = os.environ.get("MONGO_DB", "notetutor")
    jwt_secret: str = os.environ.get("JWT_SECRET", "change-me-in-production")
    jwt_minutes: int = int(os.environ.get("JWT_MINUTES", "720"))
    cookie_secure: bool = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    # The first account registered with this e-mail becomes an admin.
    admin_email: str = os.environ.get("ADMIN_EMAIL", "").lower()
    # Limits sized to the free AI tiers: page count drives quota use far
    # more than file size (10 pages is already ~13 scenes, ~7 minutes).
    max_upload_mb: int = int(os.environ.get("MAX_UPLOAD_MB", "10"))
    max_pdf_pages: int = int(os.environ.get("MAX_PDF_PAGES", "10"))
    max_text_chars: int = int(os.environ.get("MAX_TEXT_CHARS", "40000"))
    cors_origins: tuple = tuple(
        o for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o
    )


settings = Settings()
