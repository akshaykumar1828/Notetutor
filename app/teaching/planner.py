import json
import re

import httpx

from app.common.llm_client import (
    LLMCallError,
    call_groq,
    groq_available,
)
from app.teaching.prompts import TEACHING_PROMPT
from app.teaching.schemas import LessonBlueprint
from app.vision.structure_schema import NoteStructure


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "gemma3:4b-it-q4_K_M"


class TeachingPlannerError(RuntimeError):
    """Raised when lesson planning fails."""


def _extract_json(raw_response: str) -> str:

    raw_response = raw_response.strip()

    # Direct JSON
    try:
        json.loads(raw_response)
        return raw_response
    except json.JSONDecodeError:
        pass

    # Remove markdown fences
    cleaned = re.sub(
        r"```(?:json)?",
        "",
        raw_response,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.replace(
        "```",
        ""
    ).strip()

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    # Find JSON object
    start = cleaned.find("{")

    if start == -1:
        raise TeachingPlannerError(
            "Gemma did not return a JSON object.\n"
            f"Raw response:\n{raw_response}"
        )

    depth = 0
    in_string = False
    escape = False

    for index in range(
        start,
        len(cleaned)
    ):

        char = cleaned[index]

        if in_string:

            if escape:
                escape = False

            elif char == "\\":
                escape = True

            elif char == '"':
                in_string = False

            continue

        if char == '"':
            in_string = True

        elif char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:

                candidate = cleaned[
                    start:index + 1
                ]

                try:
                    json.loads(candidate)
                    return candidate

                except json.JSONDecodeError:
                    break

    raise TeachingPlannerError(
        "Gemma returned incomplete or invalid JSON.\n"
        f"Raw response:\n{raw_response}"
    )
_METHOD_MAP = {
    "conceptual explanation": "conceptual_explanation",
    "conceptual_explanation": "conceptual_explanation",
    "comparison": "comparative_analysis",
    "comparative analysis": "comparative_analysis",
    "comparative_analysis": "comparative_analysis",
    "visual diagram": "visual_diagram",
    "visual_diagram": "visual_diagram",
    "diagram": "visual_diagram",
    "visualization": "visualization",
    "example": "example",
    "worked example": "worked_example",
    "worked_example": "worked_example",
    "step-by-step derivation": "step_by_step_derivation",
    "step by step derivation": "step_by_step_derivation",
    "step_by_step_derivation": "step_by_step_derivation",
    "code": "code_walkthrough",
    "code walkthrough": "code_walkthrough",
    "code_walkthrough": "code_walkthrough",
    "process": "process_flow",
    "process flow": "process_flow",
    "process_flow": "process_flow",
    "recap": "recap",
    "list": "conceptual_explanation",
}

_VALID_METHODS = {
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
}

_VALID_DURATIONS = {"brief", "standard", "deep_dive"}


def _as_string_list(value) -> list[str]:
    """
    Coerce a value that should be list[str] into list[str],
    no matter whether Gemma sent a string, a list, or None.
    """

    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []

    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    return []


def _first_present(section: dict, *keys, default=None):
    """
    Return the value of the first key found in `section` out of
    `keys`, in order. Lets us handle every field-naming variant
    Gemma has used across different runs without branching on
    which top-level shape the whole response took.
    """

    for key in keys:
        if key in section and section[key] not in (None, ""):
            return section[key]

    return default


def _normalize_scene(section: dict, index: int) -> dict:

    # -------------------------------------------------
    # Teaching method — accept any of the field names
    # Gemma has used, in any casing/phrasing.
    # -------------------------------------------------

    approach = _first_present(
        section,
        "teaching_method",
        "teaching_approach",
        "learning_approach",
        "approach",
        default="conceptual_explanation",
    )

    if not isinstance(approach, str):
        approach = "conceptual_explanation"

    normalized_method = approach.strip()

    if normalized_method not in _VALID_METHODS:
        normalized_method = _METHOD_MAP.get(
            approach.lower().strip(),
            "conceptual_explanation",
        )

    # -------------------------------------------------
    # Duration weight
    # -------------------------------------------------

    duration_weight = _first_present(
        section,
        "duration_weight",
        default="standard",
    )

    if duration_weight not in _VALID_DURATIONS:
        duration_weight = "standard"

    # -------------------------------------------------
    # Purpose — Gemma has used purpose / description /
    # learning_objective interchangeably.
    # -------------------------------------------------

    purpose = _first_present(
        section,
        "purpose",
        "description",
        "learning_objective",
        default="",
    )

    if not isinstance(purpose, str):
        purpose = str(purpose)

    # -------------------------------------------------
    # Key points — Gemma has used key_points / content.
    # -------------------------------------------------

    key_points = _as_string_list(
        _first_present(
            section,
            "key_points",
            "content",
            "content_instructions",
            "instructions",
            default=[],
        )
    )

    # -------------------------------------------------
    # Key concepts
    # -------------------------------------------------

    key_concepts = _as_string_list(
        section.get("key_concepts", [])
    )

    # -------------------------------------------------
    # Dependencies — Gemma has used depends_on / dependencies.
    # Also handles the case where json_repair "fixed" a malformed
    # entry into a dict like {"scene_number": 1} instead of a
    # plain string — extract something usable rather than
    # stringifying the raw dict repr.
    # -------------------------------------------------

    raw_dependencies = _first_present(
        section,
        "depends_on",
        "dependencies",
        default=[],
    )

    if isinstance(raw_dependencies, str):
        raw_dependencies = [raw_dependencies]
    elif not isinstance(raw_dependencies, list):
        raw_dependencies = []

    depends_on = []

    for item in raw_dependencies:

        if isinstance(item, dict):
            if "scene_id" in item:
                depends_on.append(str(item["scene_id"]).strip())
            elif "scene_number" in item:
                depends_on.append(f"scene_{item['scene_number']}")
            # else: drop it — not a usable reference
        elif str(item).strip():
            depends_on.append(str(item).strip())

    # -------------------------------------------------
    # Visual
    # -------------------------------------------------

    visual_needed = section.get(
        "visual_needed",
        normalized_method
        in {
            "visual_diagram",
            "visualization",
            "process_flow",
        },
    )

    if not isinstance(visual_needed, bool):
        visual_needed = False

    # visual_guidance — fall back to "notes" field, which is
    # where Gemma sometimes puts presentation guidance instead.
    visual_guidance = _first_present(
        section,
        "visual_guidance",
        "notes",
        default="",
    )

    if not isinstance(visual_guidance, str):
        visual_guidance = str(visual_guidance)

    return {
        "scene_id": str(_first_present(
            section,
            "scene_id",
            "id",
            default=f"scene_{index + 1}",
        )).strip(),
        "title": str(section.get(
            "title",
            "Untitled Scene",
        )).strip(),
        "purpose": purpose,
        "teaching_method": normalized_method,
        "duration_weight": duration_weight,
        "key_concepts": key_concepts,
        "key_points": key_points,
        "visual_needed": visual_needed,
        "visual_guidance": visual_guidance,
        "depends_on": depends_on,
    }


def _normalize_lesson_blueprint(
    data: dict,
) -> dict:

    # ---------------------------------------------------------
    # 1. Remove optional wrapper
    # ---------------------------------------------------------

    if "lesson_blueprint" in data:
        data = data["lesson_blueprint"]

    # ---------------------------------------------------------
    # 2. Find the scene list under whichever key Gemma used.
    # ---------------------------------------------------------

    raw_scenes = data.get(
        "scenes",
        data.get("sections", []),
    )

    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise TeachingPlannerError(
            "Gemma returned a lesson blueprint with no "
            "usable scene list.\n"
            f"Data:\n{data}"
        )

    # FIX: the model can produce a syntactically valid array that
    # still has a stray non-dict entry mixed in (observed: an empty
    # string "" sitting between two proper scene objects). This is
    # valid JSON — json_repair won't touch it — but calling .get()
    # on a string crashes. Filter out anything that isn't a dict
    # before normalizing, rather than letting one bad entry take
    # down the whole blueprint.
    usable_scenes = [
        section for section in raw_scenes if isinstance(section, dict)
    ]

    dropped_count = len(raw_scenes) - len(usable_scenes)

    if dropped_count:
        print(
            f"[planner] Dropped {dropped_count} non-object entr"
            f"{'y' if dropped_count == 1 else 'ies'} from the "
            f"scenes list (e.g. a stray empty string) — keeping "
            f"the {len(usable_scenes)} valid scene(s)."
        )

    if not usable_scenes:
        raise TeachingPlannerError(
            "Gemma returned a scenes list with no usable "
            "(dict-shaped) entries.\n"
            f"Data:\n{data}"
        )

    scenes = [
        _normalize_scene(section, index)
        for index, section in enumerate(usable_scenes)
    ]

    # ---------------------------------------------------------
    # 3. Normalize top-level fields, filling in from `topic`
    #    whenever title/subject/audience are missing — Gemma
    #    sometimes omits them entirely, and has also used
    #    "lesson_title" as yet another naming variant.
    # ---------------------------------------------------------

    topic_fallback = _first_present(
        data,
        "topic",
        "lesson_title",
        default="Untitled Lesson",
    )

    title = _first_present(
        data, "title", "lesson_title", default=topic_fallback
    )
    subject = data.get("subject", topic_fallback)
    audience = data.get("audience", "Student")

    learning_objectives = _as_string_list(
        data.get("learning_objectives", [])
    )

    prerequisite_concepts = _as_string_list(
        data.get(
            "prerequisite_concepts",
            data.get("prerequisites", []),
        )
    )

    recap_required = data.get("recap_required", True)

    if not isinstance(recap_required, bool):
        recap_required = True

    return {
        "title": title,
        "subject": subject,
        "audience": audience,
        "learning_objectives": learning_objectives,
        "prerequisite_concepts": prerequisite_concepts,
        "scenes": scenes,
        "recap_required": recap_required,
    }

def create_lesson_plan(
    structured_note: NoteStructure,
    transcription: str,
    uncertain_content: list[str],
    max_scenes: int = 8,
    extra_instruction: str = "",
) -> LessonBlueprint:

    structured_json = (
        structured_note.model_dump_json(
            indent=2
        )
    )

    uncertain_text = "\n".join(
        f"- {item}"
        for item in uncertain_content
    )

    if not uncertain_text:
        uncertain_text = "None"

    prompt = f"""
{TEACHING_PROMPT}

FAITHFUL TRANSCRIPTION
======================
{transcription}

STRUCTURED EXTRACTION
=====================
{structured_json}

UNCERTAINTY LOG
===============
{uncertain_text}

LENGTH BUDGET
=============
Use between {max(max_scenes - 1, 3)} and {max_scenes} scenes in total
(including the opening hook and the recap). Every heading or section of
the document must be covered somewhere; group short related items into
one scene rather than dropping them or exceeding the budget.
Most scenes should be "brief" or "standard"; a short, lively lesson is
better than a long one.
{extra_instruction}
"""

    # ---------------------------------------------------------
    # Prefer Groq (a much larger hosted model) if GROQ_API_KEY is
    # set — it's far more reliable at following the JSON structure
    # instructions than the local 4B quantized model, with no
    # retries or repair needed. Falls back to local Ollama
    # automatically if Groq isn't configured or the call fails.
    # ---------------------------------------------------------

    raw_response = None

    if groq_available():

        try:
            print(
                "[planner] Using Groq (openai/gpt-oss-120b) "
                "instead of local Ollama..."
            )
            # Long documents need room for many scenes in one reply, but on
            # the free tier prompt + reply budget must stay under ~8k tokens.
            reply_budget = max(1500, min(7000, 7700 - len(prompt) // 3))
            raw_response = call_groq(prompt, temperature=0.1, max_tokens=reply_budget)

        except LLMCallError as exc:
            print(
                f"[planner] Groq call failed, falling back to "
                f"local Ollama: {exc}"
            )
            raw_response = None

    if raw_response is None:

        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 4096,
                "num_batch": 64,
                "num_predict": 2560,
            },
        }

        try:

            response = httpx.post(
                OLLAMA_URL,
                json=payload,
                timeout=180.0,
            )

            response.raise_for_status()

        except httpx.HTTPError as exc:

            raise TeachingPlannerError(
                f"Could not communicate with Ollama: {exc}"
            ) from exc

        try:

            data = response.json()

        except ValueError as exc:

            raise TeachingPlannerError(
                "Ollama returned invalid HTTP JSON."
            ) from exc

        raw_response = data.get(
            "response"
        )

    if not raw_response:

        raise TeachingPlannerError(
            f"{MODEL_NAME} returned an empty response."
        )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "RAW GEMMA PLANNER RESPONSE"
    )

    print(
        "=" * 80
    )

    print(raw_response)

    print(
        "=" * 80
    )

    try:

        # Prefer json_repair — a tolerant JSON parser built for
        # exactly this situation (a small model producing
        # almost-valid JSON, e.g. a stray "key": value pair sitting
        # directly inside an array). It fixes a much broader range
        # of malformations than our hand-rolled brace-counter can,
        # since we can't predict every way a 4B model will get JSON
        # syntax slightly wrong.
        try:
            from json_repair import repair_json

            lesson_data = repair_json(
                raw_response, return_objects=True
            )

            if not isinstance(lesson_data, dict) or not lesson_data:
                raise ValueError(
                    "json_repair produced an empty or "
                    "non-dict result"
                )

        except ImportError:
            # json_repair not installed — fall back to the manual
            # extractor. Run `pip install json-repair` for the
            # more robust path.
            json_text = _extract_json(raw_response)
            lesson_data = json.loads(json_text)

        except Exception:
            # json_repair itself choked — fall back to the manual
            # extractor rather than giving up immediately.
            json_text = _extract_json(raw_response)
            lesson_data = json.loads(json_text)

        lesson_data = _normalize_lesson_blueprint(
            lesson_data
        )

        return LessonBlueprint.model_validate(
            lesson_data
        )


    except TeachingPlannerError:

        raise

    except Exception as exc:

        raise TeachingPlannerError(
            f"{MODEL_NAME} returned an invalid "
            "lesson blueprint.\n\n"
            f"Raw response:\n{raw_response}\n\n"
            f"Validation error: {exc}"
        ) from exc