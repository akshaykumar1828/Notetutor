from pydantic import BaseModel, Field, field_validator


def _flatten(value) -> str:
    """Turns nested model output (dicts/lists) into readable text."""
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values() if v not in (None, ""))
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value if v not in (None, ""))
    return str(value).strip()


class SceneContent(BaseModel):
    scene_id: str

    explanation: str

    key_takeaways: list[str] = Field(
        default_factory=list
    )

    example: str | None = None

    misconception_correction: str | None = None

    visual_needed: bool = False

    visual_instruction: str | None = None

    # Models sometimes return a structured object or a list where plain
    # text is expected; accept the content rather than failing the lesson.
    @field_validator(
        "scene_id", "explanation", "example", "misconception_correction",
        "visual_instruction", mode="before",
    )
    @classmethod
    def _as_text(cls, value):
        if value is None or isinstance(value, str):
            return value
        return _flatten(value)

    @field_validator("key_takeaways", mode="before")
    @classmethod
    def _as_text_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            value = list(value.values())
        return [_flatten(v) for v in value if v not in (None, "")]
