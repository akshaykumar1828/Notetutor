from pydantic import BaseModel, Field


class NoteSection(BaseModel):
    """
    A logical section identified inside the notes.
    """

    title: str

    content: list[str] = Field(
        default_factory=list
    )


class NoteStructure(BaseModel):
    """
    Structured representation of the uploaded notes.
    """

    topic: str

    sections: list[NoteSection] = Field(
        default_factory=list
    )