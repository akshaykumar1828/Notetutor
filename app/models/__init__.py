"""Models package for NoteTutor AI.

Exposes the document ingestion data contract (Milestone 2A).
"""

from app.models.document import Document, FileType, Page

__all__ = ["Document", "FileType", "Page"]
