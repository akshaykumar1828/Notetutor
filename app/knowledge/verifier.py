import json

import httpx

from app.common.llm_client import LLMCallError, call_groq, groq_available
from app.knowledge.prompts import KNOWLEDGE_VERIFICATION_PROMPT
from app.knowledge.schemas import ContentVerification, KnowledgeVerification
from app.vision.structure_schema import NoteStructure
from app.content.schemas import SceneContent


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5vl:3b"


class KnowledgeVerificationError(RuntimeError):
    """Raised when knowledge verification fails."""


# ======================================================================
# EXISTING: verifies the RAW NOTES, before planning/generation.
# ======================================================================

def verify_knowledge(
    structured_note: NoteStructure,
    uncertain_content: list[str],
) -> KnowledgeVerification:

    structured_json = structured_note.model_dump_json(
        indent=2
    )

    uncertain_text = "\n".join(
        f"- {item}"
        for item in uncertain_content
    )

    prompt = f"""
{KNOWLEDGE_VERIFICATION_PROMPT}

STUDENT STRUCTURED NOTES:
-------------------------
{structured_json}

UNCERTAIN TRANSCRIPTION:
------------------------
{uncertain_text}
"""

    # This is a pure TEXT task (checking claims in structured notes),
    # not vision — no reason to route it through a vision model, and
    # Groq's much larger hosted model is both faster and more
    # reliable for this kind of structured judgment call.
    raw_response = None

    if groq_available():
        try:
            print(
                "[verifier] Using Groq (openai/gpt-oss-120b) "
                "instead of local Ollama..."
            )
            raw_response = call_groq(prompt, temperature=0.1)

        except LLMCallError as exc:
            print(
                f"[verifier] Groq call failed, falling back to "
                f"local Ollama: {exc}"
            )
            raw_response = None

    if raw_response is None:

        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        try:
            response = httpx.post(
                OLLAMA_URL,
                json=payload,
                timeout=180.0,
            )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise KnowledgeVerificationError(
                f"Ollama returned HTTP {response.status_code}.\n"
                f"Response:\n{response.text}"
            ) from exc

        except httpx.HTTPError as exc:
            raise KnowledgeVerificationError(
                f"Could not communicate with Ollama: {exc}"
            ) from exc

        data = response.json()
        raw_response = data.get("response")

    if not raw_response:
        raise KnowledgeVerificationError(
            "Model returned an empty response."
        )

    try:
        return KnowledgeVerification.model_validate_json(
            raw_response
        )

    except Exception as exc:
        raise KnowledgeVerificationError(
            "Invalid knowledge verification JSON.\n"
            f"Raw response:\n{raw_response}\n\n"
            f"Validation error: {exc}"
        ) from exc


# ======================================================================
# NEW: verifies GENERATED SCENE CONTENT against the original notes,
# after generate_scene_content() runs. This is the missing
# Milestone 3C step — nothing previously checked model output against
# source material after generation.
# ======================================================================

POST_GENERATION_VERIFICATION_PROMPT = """
You are a strict fact-checker for NoteTutor AI.

You will be given:
1. The student's ORIGINAL STRUCTURED NOTES (the source of truth for
   what the student actually wrote and how they categorized things).
2. GENERATED SCENE CONTENT that an AI teacher produced for a video
   lesson, supposedly based on those notes.

Your ONLY job is to check whether the generated content:

- Correctly preserves any categories, comparisons, or distinctions
  the notes explicitly make (e.g. "X vs Y", two separate lists).
- Does NOT place an item, example, or fact under the wrong category
  relative to how the notes categorized it.
- Does NOT contradict the notes without it being an intentional,
  clearly-justified correction of a real error in the notes.

Do NOT penalize the generated content for:
- adding reasonable extra explanation
- rephrasing in clearer language
- correcting a genuine factual error in the notes (this is allowed
  and expected — but only when the notes are actually wrong, not when
  the generated content just misused a correct distinction)

Return ONLY valid JSON matching this structure:

{
  "is_accurate": true,
  "misplaced_claims": [
    "string describing any claim that mismatches its category in the notes"
  ],
  "corrected_explanation": null
}

If is_accurate is false, "corrected_explanation" must contain a
corrected version of the explanation field that fixes the misplaced
claims while keeping everything else intact. Otherwise leave it null.

Do not output Markdown. Do not output ```json. Do not output anything
outside the JSON object.
"""


def verify_generated_content(
    scene_content: SceneContent,
    structured_note: NoteStructure,
) -> ContentVerification:

    notes_json = structured_note.model_dump_json(indent=2)
    content_json = scene_content.model_dump_json(indent=2)

    prompt = f"""
{POST_GENERATION_VERIFICATION_PROMPT}

ORIGINAL STRUCTURED NOTES:
---------------------------
{notes_json}

GENERATED SCENE CONTENT:
-------------------------
{content_json}
"""

    raw_response = None

    if groq_available():
        try:
            print(
                "[verifier] Using Groq (openai/gpt-oss-120b) "
                "instead of local Ollama..."
            )
            raw_response = call_groq(prompt, temperature=0.1)

        except LLMCallError as exc:
            print(
                f"[verifier] Groq call failed, falling back to "
                f"local Ollama: {exc}"
            )
            raw_response = None

    if raw_response is None:

        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        try:
            response = httpx.post(
                OLLAMA_URL,
                json=payload,
                timeout=180.0,
            )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise KnowledgeVerificationError(
                f"Ollama returned HTTP {response.status_code}.\n"
                f"Response:\n{response.text}"
            ) from exc

        except httpx.HTTPError as exc:
            raise KnowledgeVerificationError(
                f"Could not communicate with Ollama: {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise KnowledgeVerificationError(
                "Ollama returned invalid HTTP JSON."
            ) from exc

        raw_response = data.get("response")

    if not raw_response:
        raise KnowledgeVerificationError(
            "Model returned an empty post-generation "
            "verification response."
        )

    try:
        return ContentVerification.model_validate_json(raw_response)

    except Exception as exc:
        raise KnowledgeVerificationError(
            "Invalid content verification JSON.\n"
            f"Raw response:\n{raw_response}\n\n"
            f"Validation error: {exc}"
        ) from exc