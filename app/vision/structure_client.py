from app.vision.structure_extractor import extract_structure
from app.vision.structure_schema import NoteStructure


def structure_note(
    transcription: str,
) -> NoteStructure:
    """
    Adapter matching the `structure_note` name used by the pipeline.
    Wraps the real implementation in structure_extractor.py.
    """

    return extract_structure(transcription)
