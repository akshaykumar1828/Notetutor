import json
import re

import httpx

from app.vision.structure_schema import (
    NoteStructure,
)


OLLAMA_URL = "http://localhost:11434/api/generate"

MODEL_NAME = "gemma3:4b-it-q4_K_M"


class StructureExtractionError(RuntimeError):
    """Raised when note structure extraction fails."""


STRUCTURE_PROMPT = """
You are the structure extraction agent for NoteTutor AI.

You will receive a transcription of educational notes.

Your job is to organize the existing information into a
simple structured representation.

IMPORTANT RULES:

1. Do NOT add new facts.
2. Do NOT add examples that are not in the notes.
3. Do NOT change the meaning of the notes.
4. Group related information into logical sections.
5. Identify the main topic.
6. Keep the original educational meaning.
7. Use concise section titles.
8. Every section must contain information actually
   present in the transcription.
9. If a word is clearly a transcription misspelling of a
   term that appears spelled correctly elsewhere in the
   same transcription, use the correct spelling
   consistently. Never introduce a term that does not
   appear in the transcription.

Return ONLY valid JSON in this format:

{
  "topic": "main topic",
  "sections": [
    {
      "title": "section title",
      "content": [
        "information from notes"
      ]
    }
  ]
}

TRANSCRIPTION:
"""


def _extract_json(
    raw_response: str,
) -> str:
    """
    Extract a JSON object from a model response.
    """

    raw_response = raw_response.strip()

    # Direct JSON.
    try:
        json.loads(raw_response)
        return raw_response

    except json.JSONDecodeError:
        pass

    # Remove markdown fences.
    cleaned = re.sub(
        r"```(?:json)?",
        "",
        raw_response,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.replace(
        "```",
        "",
    ).strip()

    try:
        json.loads(cleaned)
        return cleaned

    except json.JSONDecodeError:
        pass

    # Find first balanced JSON object.
    start = cleaned.find("{")

    if start == -1:
        raise StructureExtractionError(
            "No JSON object found in model response."
        )

    depth = 0
    in_string = False
    escape = False

    for index in range(
        start,
        len(cleaned),
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

    raise StructureExtractionError(
        "Model returned incomplete or invalid JSON."
    )


def extract_structure(
    transcription: str,
) -> NoteStructure:

    if not transcription or not transcription.strip():
        raise StructureExtractionError(
            "Cannot extract structure from empty transcription."
        )

    prompt = (
        STRUCTURE_PROMPT
        + "\n"
        + transcription
    )

    schema = NoteStructure.model_json_schema()

    from app.common.llm_client import LLMCallError, generate_json, groq_available

    if groq_available():
        try:
            return NoteStructure.model_validate(
                generate_json(prompt, schema=schema, label="structure")
            )
        except (LLMCallError, ValueError) as exc:
            print(f"[structure] Groq failed, using local model: {exc}")

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
            "num_predict": 2048,
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
        raise StructureExtractionError(
            f"Could not communicate with Ollama: {exc}"
        ) from exc

    try:
        data = response.json()

    except ValueError as exc:
        raise StructureExtractionError(
            "Ollama returned invalid HTTP JSON."
        ) from exc

    raw_response = data.get(
        "response"
    )

    if not raw_response:
        raise StructureExtractionError(
            "Structure model returned an empty response."
        )

    try:

        json_text = _extract_json(
            raw_response
        )

        result = json.loads(
            json_text
        )

        return NoteStructure.model_validate(
            result
        )

    except StructureExtractionError:
        raise

    except Exception as exc:
        raise StructureExtractionError(
            "Invalid structure returned by model.\n"
            f"Raw response:\n{raw_response}\n\n"
            f"Validation error: {exc}"
        ) from exc