"""Tests for the document ingestion data contract (Milestone 2A).

These tests cover schema validation only. No PDF parsing, OCR, image
processing, or model/LLM calls are exercised here.
"""

import pytest
from pydantic import ValidationError

from app.models.document import Document, FileType, Page


def _make_page(page_number: int = 1) -> Page:
    return Page(
        page_number=page_number,
        image_path=f"/tmp/pages/page-{page_number}.png",
        width=800,
        height=1200,
    )


def test_valid_document_creation():
    """A document with matching page_count and pages is accepted."""
    document = Document(
        document_id="doc-1",
        original_filename="notes.pdf",
        file_type=FileType.PDF,
        page_count=1,
        pages=[_make_page(1)],
    )

    assert document.document_id == "doc-1"
    assert document.file_type == FileType.PDF
    assert document.page_count == 1
    assert len(document.pages) == 1


def test_valid_page_creation():
    """A page with all fields populated is accepted."""
    page = _make_page(page_number=3)

    assert page.page_number == 3
    assert page.image_path == "/tmp/pages/page-3.png"
    assert page.width == 800
    assert page.height == 1200
    assert page.metadata == {}


def test_document_with_multiple_pages():
    """A document can hold several pages in explicit order."""
    pages = [_make_page(1), _make_page(2), _make_page(3)]
    document = Document(
        document_id="doc-multi",
        original_filename="scan.pdf",
        file_type=FileType.PDF,
        page_count=3,
        pages=pages,
    )

    assert [page.page_number for page in document.pages] == [1, 2, 3]


def test_document_without_pages_is_allowed():
    """A document may be created before its pages are materialized."""
    document = Document(
        document_id="doc-no-pages",
        original_filename="scan.pdf",
        file_type=FileType.PDF,
        page_count=5,
    )

    assert document.pages == []
    assert document.page_count == 5


def test_invalid_empty_document_id_rejected():
    """An empty document_id must be rejected."""
    with pytest.raises(ValidationError):
        Document(
            document_id="",
            original_filename="notes.pdf",
            file_type=FileType.PDF,
            page_count=1,
        )


def test_invalid_empty_filename_rejected():
    """An empty original_filename must be rejected."""
    with pytest.raises(ValidationError):
        Document(
            document_id="doc-1",
            original_filename="   ",
            file_type=FileType.PDF,
            page_count=1,
        )


def test_invalid_page_count_rejected():
    """A zero or negative page_count must be rejected."""
    with pytest.raises(ValidationError):
        Document(
            document_id="doc-1",
            original_filename="notes.pdf",
            file_type=FileType.PDF,
            page_count=0,
        )


@pytest.mark.parametrize("page_number", [0, -1])
def test_invalid_page_number_rejected(page_number):
    """Zero or negative page numbers must be rejected."""
    with pytest.raises(ValidationError):
        Page(
            page_number=page_number,
            image_path="/tmp/pages/page.png",
        )


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 100), (100, 0), (-1, 100), (100, -1)],
)
def test_invalid_page_dimensions_rejected(width, height):
    """Zero or negative width/height must be rejected."""
    with pytest.raises(ValidationError):
        Page(
            page_number=1,
            image_path="/tmp/pages/page.png",
            width=width,
            height=height,
        )


def test_invalid_file_type_rejected():
    """A file_type outside the controlled enum must be rejected."""
    with pytest.raises(ValidationError):
        Document(
            document_id="doc-1",
            original_filename="notes.docx",
            file_type="docx",
            page_count=1,
        )


def test_default_metadata_does_not_share_state_between_instances():
    """Each Page must get its own metadata dict, not a shared default."""
    page_one = Page(page_number=1, image_path="/tmp/pages/page-1.png")
    page_two = Page(page_number=2, image_path="/tmp/pages/page-2.png")

    page_one.metadata["source"] = "scanner"

    assert page_one.metadata == {"source": "scanner"}
    assert page_two.metadata == {}


def test_page_count_inconsistent_with_pages_rejected():
    """page_count must match the number of supplied pages when present."""
    with pytest.raises(ValidationError):
        Document(
            document_id="doc-1",
            original_filename="notes.pdf",
            file_type=FileType.PDF,
            page_count=2,
            pages=[_make_page(1)],
        )
