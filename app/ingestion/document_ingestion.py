"""PDF and image document ingestion for NoteTutor AI.

This module converts supported input files into the standardized
Document/Page Pydantic models defined in app.models.document.

Responsibilities:
    - Validate supported input files.
    - Preserve the original uploaded file.
    - Render PDF pages to images.
    - Validate image inputs.
    - Create standardized Page metadata.
    - Return a validated Document object.

This module intentionally does NOT perform OCR, LLM calls, embeddings,
RAG, or any other AI processing.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pymupdf
from PIL import Image, UnidentifiedImageError

from app.models.document import Document, FileType, Page
from app.common.paths import work_dir


# Project-relative default location for generated page representations.
DEFAULT_PROCESSED_DIR = work_dir("processed")

# Rendering PDFs at 200 DPI gives downstream vision models reasonably
# detailed page images without producing unnecessarily huge files.
PDF_RENDER_DPI = 200


class DocumentIngestionError(Exception):
    """Raised when a document cannot be safely ingested."""


def _get_file_type(file_path: Path) -> FileType:
    """Return the supported FileType based on the file extension."""
    extension = file_path.suffix.lower().lstrip(".")

    try:
        return FileType(extension)
    except ValueError as exc:
        supported = ", ".join(item.value for item in FileType)
        raise DocumentIngestionError(
            f"Unsupported file type '.{extension}'. "
            f"Supported types: {supported}."
        ) from exc


def _safe_stem(filename: str) -> str:
    """Create a safe filename stem for generated page files."""
    stem = Path(filename).stem

    # Keep only characters that are safe and predictable in filenames.
    safe_characters = [
        character if character.isalnum() or character in "-_"
        else "_"
        for character in stem
    ]

    cleaned = "".join(safe_characters).strip("._")

    return cleaned or "document"


def _create_document_id() -> str:
    """Create a unique identifier for an ingested document."""
    return uuid.uuid4().hex


def _prepare_output_directory(output_dir: Path) -> Path:
    """Create and return the processed-output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _ingest_image(
    file_path: Path,
    output_dir: Path,
    document_id: str,
) -> list[Page]:
    """Validate and copy a single image into the processed directory."""
    try:
        with Image.open(file_path) as image:
            image.verify()

        # Reopen after verify() because verify() invalidates the image
        # object for further operations.
        with Image.open(file_path) as image:
            width, height = image.size

    except (UnidentifiedImageError, OSError) as exc:
        raise DocumentIngestionError(
            f"Invalid or unreadable image file: {file_path.name}"
        ) from exc

    safe_stem = _safe_stem(file_path.name)
    output_filename = f"{document_id}_page_001{file_path.suffix.lower()}"
    output_path = output_dir / output_filename

    # Copy rather than modify the source file. This preserves the exact
    # original upload for future multimodal processing.
    shutil.copy2(file_path, output_path)

    return [
        Page(
            page_number=1,
            image_path=str(output_path),
            width=width,
            height=height,
            metadata={
                "source_type": "image",
                "original_stem": safe_stem,
            },
        )
    ]


def _ingest_pdf(
    file_path: Path,
    output_dir: Path,
    document_id: str,
) -> list[Page]:
    """Render every page of a PDF into a PNG image."""
    try:
        pdf = pymupdf.open(file_path)
    except Exception as exc:
        raise DocumentIngestionError(
            f"Unable to open PDF: {file_path.name}"
        ) from exc

    try:
        if pdf.page_count == 0:
            raise DocumentIngestionError(
                f"PDF contains no pages: {file_path.name}"
            )

        pages: list[Page] = []

        scale = PDF_RENDER_DPI / 72.0
        matrix = pymupdf.Matrix(scale, scale)

        for index in range(pdf.page_count):
            page_number = index + 1
            pdf_page = pdf.load_page(index)

            try:
                pixmap = pdf_page.get_pixmap(
                    matrix=matrix,
                    alpha=False,
                )
            except Exception as exc:
                raise DocumentIngestionError(
                    f"Unable to render page {page_number} "
                    f"of PDF '{file_path.name}'."
                ) from exc

            output_filename = (
                f"{document_id}_page_{page_number:03d}.png"
            )
            output_path = output_dir / output_filename

            pixmap.save(output_path)

            pages.append(
                Page(
                    page_number=page_number,
                    image_path=str(output_path),
                    width=pixmap.width,
                    height=pixmap.height,
                    metadata={
                        "source_type": "pdf",
                        "source_page_index": index,
                        "render_dpi": PDF_RENDER_DPI,
                    },
                )
            )

        return pages

    finally:
        pdf.close()


def ingest_document(
    file_path: str | Path,
    output_dir: str | Path = DEFAULT_PROCESSED_DIR,
) -> Document:
    """Ingest one supported PDF or image file.

    Args:
        file_path:
            Path to the original uploaded PDF or image.

        output_dir:
            Directory where generated page representations are stored.

    Returns:
        A validated Document containing page metadata.

    Raises:
        DocumentIngestionError:
            If the input does not exist, is unsupported, is invalid,
            or cannot be processed safely.
    """
    source_path = Path(file_path)

    if not source_path.exists():
        raise DocumentIngestionError(
            f"Input file does not exist: {source_path}"
        )

    if not source_path.is_file():
        raise DocumentIngestionError(
            f"Input path is not a file: {source_path}"
        )

    file_type = _get_file_type(source_path)
    document_id = _create_document_id()
    processed_dir = _prepare_output_directory(Path(output_dir))

    if file_type == FileType.PDF:
        pages = _ingest_pdf(
            source_path,
            processed_dir,
            document_id,
        )
    else:
        pages = _ingest_image(
            source_path,
            processed_dir,
            document_id,
        )

    return Document(
        document_id=document_id,
        original_filename=source_path.name,
        file_type=file_type,
        page_count=len(pages),
        pages=pages,
    )