"""Document ingestion data contract (Milestone 2A).

This module defines the Pydantic schemas that represent an uploaded
document and its pages as *metadata only*. No parsing, OCR, image
processing, or storage of binary content happens here or anywhere in
this milestone. These models exist purely to establish a stable,
validated internal representation that later milestones (PDF/image
ingestion, multimodal understanding, etc.) can build on without
changing the contract.

Design decisions:
    * ``FileType`` is a ``str`` subclass ``Enum`` so it serializes to a
      plain string in JSON while still constraining allowed values.
      Adding a new supported format later only requires adding a new
      enum member.
    * ``Page.metadata`` and any other mutable defaults use
      ``default_factory`` so each model instance gets its own dict
      instead of sharing one mutable object across instances.
    * ``Document`` only stores a list of ``Page`` objects (metadata),
      never raw bytes. ``image_path`` on ``Page`` is a plain string
      reference to where a rendered page image *would* live; this
      milestone does not create, read, or validate that the path
      actually exists on disk.
    * ``Document`` validates that ``page_count`` matches the number of
      supplied ``pages`` whenever pages are provided. This keeps the
      contract internally consistent without requiring a complex
      validator.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class FileType(str, Enum):
    """Controlled set of document file types supported by ingestion.

    This only defines the *allowed representation*. MIME-type
    detection and actual file parsing are implemented in a later
    milestone.
    """

    PDF = "pdf"
    PNG = "png"
    JPG = "jpg"
    JPEG = "jpeg"


class Page(BaseModel):
    """Metadata describing a single page of an ingested document.

    This is a data-contract object only. It does not contain image
    bytes and does not trigger any image processing.
    """

    page_number: int = Field(
        gt=0,
        description="1-based position of this page within the document.",
    )
    image_path: str = Field(
        min_length=1,
        description="Path where the rendered page image is expected to live.",
    )
    width: Optional[int] = Field(
        default=None,
        gt=0,
        description="Optional page width in pixels.",
    )
    height: Optional[int] = Field(
        default=None,
        gt=0,
        description="Optional page height in pixels.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Flexible bag for future page-specific information.",
    )

    @field_validator("image_path")
    @classmethod
    def image_path_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only image paths."""
        if not value.strip():
            raise ValueError("image_path must not be empty or blank")
        return value


class Document(BaseModel):
    """Metadata describing one uploaded document and its pages.

    This is a data-contract object only. It does not contain PDF or
    image bytes and does not trigger any parsing, OCR, or model calls.
    """

    document_id: str = Field(
        min_length=1,
        description="Unique identifier for the uploaded document.",
    )
    original_filename: str = Field(
        min_length=1,
        description="Filename as originally uploaded by the student.",
    )
    file_type: FileType = Field(description="Controlled document file type.")
    page_count: int = Field(
        gt=0,
        description="Total number of pages expected in the document.",
    )
    pages: List[Page] = Field(
        default_factory=list,
        description="Ordered metadata for each page in the document.",
    )

    @field_validator("document_id", "original_filename")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only identifiers and filenames."""
        if not value.strip():
            raise ValueError("value must not be empty or blank")
        return value

    @model_validator(mode="after")
    def page_count_matches_pages(self) -> "Document":
        """Ensure page_count agrees with the supplied pages, if any.

        Pages are optional at construction time (a document could be
        registered before its pages are materialized in a later
        milestone), so this only enforces consistency when at least
        one page is supplied.
        """
        if self.pages and len(self.pages) != self.page_count:
            raise ValueError(
                "page_count "
                f"({self.page_count}) does not match the number of "
                f"pages provided ({len(self.pages)})"
            )
        return self
