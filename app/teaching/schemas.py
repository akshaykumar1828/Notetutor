from typing import List, Literal

from pydantic import BaseModel, Field


TeachingMethod = Literal[
    "conceptual_explanation",
    "example",
    "comparative_analysis",
    "visual_diagram",
    "visualization",
    "step_by_step_derivation",
    "worked_example",
    "code_walkthrough",
    "process_flow",
    "recap",
]


DurationWeight = Literal[
    "brief",
    "standard",
    "deep_dive",
]


class ScenePlan(BaseModel):
    scene_id: str

    title: str

    purpose: str

    teaching_method: TeachingMethod

    duration_weight: DurationWeight

    key_concepts: List[str] = Field(
        default_factory=list
    )

    key_points: List[str] = Field(
        default_factory=list
    )

    visual_needed: bool

    visual_guidance: str

    depends_on: List[str] = Field(
        default_factory=list
    )


class LessonBlueprint(BaseModel):
    title: str

    subject: str

    audience: str

    learning_objectives: List[str] = Field(
        default_factory=list
    )

    prerequisite_concepts: List[str] = Field(
        default_factory=list
    )

    scenes: List[ScenePlan] = Field(
        default_factory=list
    )

    recap_required: bool