from pathlib import Path

from pydantic import BaseModel, Field

from app.vision.transcription import transcribe_image


class TranscriptionResult(BaseModel):
    """
    Result of transcribing a note image.

    NOTE: uncertain_content and confidence are NOT currently backed
    by real detection logic. transcribe_image() only returns plain
    text today. These fields exist so downstream code (the teaching
    planner's uncertainty-handling section) has somewhere to read
    from, but they are placeholders until real uncertainty detection
    is implemented in the vision layer (e.g. having the vision model
    flag low-confidence spans directly in its own output).

    Do not treat confidence=None or an empty uncertain_content list
    as "the model was fully confident" — it means "not measured yet."
    """

    transcription: str

    uncertain_content: list[str] = Field(
        default_factory=list
    )

    confidence: float | None = None


def transcribe_note(
    image_path: str | Path,
) -> TranscriptionResult:
    """
    Adapter around transcribe_image() matching the interface
    expected by the test scripts and downstream pipeline.
    """

    transcription = transcribe_image(
        image_path
    )

    return TranscriptionResult(
        transcription=transcription,
        uncertain_content=[],
        confidence=None,
    )