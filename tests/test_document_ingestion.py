"""Tests for Milestone 2B document ingestion."""

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from app.ingestion.document_ingestion import (
    DocumentIngestionError,
    ingest_document,
)


def create_test_image(
    path: Path,
    size: tuple[int, int] = (800, 600),
) -> None:
    """Create a temporary valid PNG image."""
    image = Image.new("RGB", size, "white")
    image.save(path, format="PNG")


def create_test_pdf(
    path: Path,
    page_count: int = 3,
) -> None:
    """Create a valid temporary multi-page PDF."""
    pdf = pymupdf.open()

    for index in range(page_count):
        page = pdf.new_page(width=600, height=800)
        page.insert_text(
            (72, 100),
            f"Test page {index + 1}",
            fontsize=20,
        )

    pdf.save(path)
    pdf.close()


def test_ingest_single_png(tmp_path: Path) -> None:
    """A valid PNG becomes a one-page Document."""
    source = tmp_path / "notes.png"
    create_test_image(source, (800, 600))

    output_dir = tmp_path / "processed"

    document = ingest_document(source, output_dir)

    assert document.page_count == 1
    assert document.original_filename == "notes.png"
    assert document.file_type.value == "png"
    assert len(document.pages) == 1

    page = document.pages[0]

    assert page.page_number == 1
    assert page.width == 800
    assert page.height == 600
    assert Path(page.image_path).exists()


@pytest.mark.parametrize("extension", ["jpg", "jpeg"])
def test_ingest_jpeg_images(
    tmp_path: Path,
    extension: str,
) -> None:
    """JPEG and JPG inputs are accepted."""
    source = tmp_path / f"notes.{extension}"

    image = Image.new("RGB", (640, 480), "white")
    image.save(source, format="JPEG")

    document = ingest_document(
        source,
        tmp_path / "processed",
    )

    assert document.page_count == 1
    assert document.file_type.value == extension
    assert document.pages[0].width == 640
    assert document.pages[0].height == 480
    assert Path(document.pages[0].image_path).exists()


def test_ingest_multi_page_pdf(tmp_path: Path) -> None:
    """A multi-page PDF preserves page count and order."""
    source = tmp_path / "lecture_notes.pdf"
    create_test_pdf(source, page_count=3)

    output_dir = tmp_path / "processed"

    document = ingest_document(source, output_dir)

    assert document.page_count == 3
    assert document.file_type.value == "pdf"
    assert len(document.pages) == 3

    page_numbers = [
        page.page_number
        for page in document.pages
    ]

    assert page_numbers == [1, 2, 3]

    for page in document.pages:
        assert Path(page.image_path).exists()
        assert page.width > 0
        assert page.height > 0
        assert page.metadata["source_type"] == "pdf"


def test_original_file_is_preserved(tmp_path: Path) -> None:
    """Ingestion must not modify the original uploaded image."""
    source = tmp_path / "original.png"
    create_test_image(source)

    original_bytes = source.read_bytes()

    ingest_document(
        source,
        tmp_path / "processed",
    )

    assert source.exists()
    assert source.read_bytes() == original_bytes


def test_missing_file_raises_error(tmp_path: Path) -> None:
    """A missing input path should fail clearly."""
    source = tmp_path / "does_not_exist.png"

    with pytest.raises(DocumentIngestionError, match="does not exist"):
        ingest_document(source, tmp_path / "processed")


def test_unsupported_file_type_raises_error(
    tmp_path: Path,
) -> None:
    """Unsupported extensions should be rejected."""
    source = tmp_path / "notes.txt"
    source.write_text("This is not an image or PDF.")

    with pytest.raises(
        DocumentIngestionError,
        match="Unsupported file type",
    ):
        ingest_document(source, tmp_path / "processed")


def test_invalid_image_raises_error(tmp_path: Path) -> None:
    """Corrupt image content should be rejected."""
    source = tmp_path / "corrupt.png"
    source.write_bytes(b"this is not a valid image")

    with pytest.raises(
        DocumentIngestionError,
        match="Invalid or unreadable image",
    ):
        ingest_document(source, tmp_path / "processed")


def test_invalid_pdf_raises_error(tmp_path: Path) -> None:
    """Invalid PDF content should be rejected."""
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"this is not a valid PDF")

    with pytest.raises(
        DocumentIngestionError,
        match="Unable to open PDF",
    ):
        ingest_document(source, tmp_path / "processed")


def test_empty_pdf_is_rejected(tmp_path: Path) -> None:
    """A PDF with zero pages should be rejected cleanly.

    PyMuPDF cannot save a zero-page PDF, so construct the invalid
    fixture as malformed/empty bytes instead of trying to save one.
    """
    source = tmp_path / "empty.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(DocumentIngestionError):
        ingest_document(source, tmp_path / "processed")


def test_document_ids_are_unique(tmp_path: Path) -> None:
    """Separate ingestion operations receive different IDs."""
    source = tmp_path / "notes.png"
    create_test_image(source)

    output_dir = tmp_path / "processed"

    first = ingest_document(source, output_dir)
    second = ingest_document(source, output_dir)

    assert first.document_id != second.document_id