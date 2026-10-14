from typing import List, Literal

from pydantic import BaseModel, Field


class ClaimCheck(BaseModel):
    claim: str = Field(min_length=1)

    status: Literal[
        "correct",
        "ambiguous",
        "potentially_incorrect",
    ]

    explanation: str = Field(min_length=1)

    correction: str | None = None


class KnowledgeVerification(BaseModel):
    verified_topic: str = Field(min_length=1)

    claims: List[ClaimCheck] = Field(
        default_factory=list
    )

    overall_notes: List[str] = Field(
        default_factory=list
    )


# NEW: used by verify_generated_content() in verifier.py.
# Checks GENERATED scene content against the notes, after generation,
# instead of only checking the raw notes before planning.
class ContentVerification(BaseModel):
    is_accurate: bool

    misplaced_claims: List[str] = Field(
        default_factory=list
    )

    corrected_explanation: str | None = None