"""
Structured visual plan for one scene.

Every visual element carries the index of the narration sentence that
introduces it, so the renderer can reveal and highlight it exactly when
the narrator talks about it. Only the fields for the chosen
visual_type are filled; the rest stay empty.
"""

from typing import Literal

from pydantic import BaseModel, Field


VisualType = Literal[
    "concept_map",
    "process_flow",
    "comparison",
    "key_points",
    "illustration",
]


class ConceptNode(BaseModel):
    id: str
    label: str
    icon: str = ""
    sentence: int = -1


class ConceptEdge(BaseModel):
    source: str
    target: str
    label: str = ""


class ProcessStep(BaseModel):
    id: str
    label: str
    detail: str = ""
    icon: str = ""
    sentence: int = -1


class ComparisonRow(BaseModel):
    id: str
    criterion: str
    cells: list[str] = Field(default_factory=list)
    sentence: int = -1


class KeyPoint(BaseModel):
    id: str
    text: str
    kind: Literal["text", "formula", "code"] = "text"
    icon: str = ""
    sentence: int = -1


class VisualPlan(BaseModel):
    scene_id: str = ""

    visual_type: VisualType

    reason: str = ""

    # concept_map
    nodes: list[ConceptNode] = Field(default_factory=list)
    edges: list[ConceptEdge] = Field(default_factory=list)

    # process_flow
    steps: list[ProcessStep] = Field(default_factory=list)

    # comparison
    columns: list[str] = Field(default_factory=list)
    rows: list[ComparisonRow] = Field(default_factory=list)

    # key_points
    points: list[KeyPoint] = Field(default_factory=list)

    # illustration: a real licensed picture plus `points` beside it.
    # image_path / image_credit are filled by retrieval, not the LLM.
    image_query: str = ""
    image_path: str = ""
    image_credit: str = ""

    def element_sentences(self) -> dict[str, int]:
        """Reveal sentence for every element that has its own cue."""
        elements = {
            "concept_map": self.nodes,
            "process_flow": self.steps,
            "comparison": self.rows,
            "key_points": self.points,
            "illustration": self.points,
        }[self.visual_type]
        return {element.id: element.sentence for element in elements}
