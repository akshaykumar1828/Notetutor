import re
from pathlib import Path

from app.common.llm_client import LLMCallError, call_groq_vision, groq_available
from app.vision.vision_client import (
    VisionClientError,
    generate_vision_response,
)


class TranscriptionError(RuntimeError):
    """Raised when note transcription fails."""


TRANSCRIPTION_PROMPT = """
You are the transcription agent for NoteTutor AI.

Your task is to accurately transcribe the educational
content visible in the provided note image.

IMPORTANT RULES:

1. Read only what is actually visible in the image.
2. Do not invent missing words.
3. Do not add explanations.
4. Do not summarize.
5. Preserve mathematical expressions as accurately as possible.
6. Preserve headings and bullet points when visible.
7. Preserve code when visible.
8. If handwriting is unclear, make the best possible
   transcription without inventing unrelated information.
9. Do not answer questions contained in the notes.
10. Return only the transcription.
11. TECHNICAL TERMINOLOGY

When the notes contain technical terms, frameworks,
programming languages, libraries, model names, mathematical
notation, or other domain-specific terminology:

- inspect the surrounding visual context carefully
- preserve the terminology exactly when it is clearly readable
- do not replace a clearly written term with a different
  preferred term
- if a technical term is visually ambiguous, use surrounding
  context only when the intended term is strongly supported
- if the term cannot be resolved confidently, preserve the
  uncertainty rather than inventing a replacement

Technical terminology must not be normalized merely because
another term seems more familiar.

12. LAYOUT AND ARROWS

Notes often link a statement to the term it describes with an arrow,
a bracket or a line instead of writing the term again (for example a
heading "A vs B" with arrows from "A" and from "B" to two different
statements). The arrow carries meaning, so do NOT drop it.

Follow each arrow/bracket/line to the term it starts from, and write
the linked statement prefixed with that term, in this exact form:

    <term> -> <statement>

Do this for every linked statement. Never output a statement that
starts with a bare bracket, arrow or "is about..." without its term.
If you truly cannot tell which term a statement is linked to, write
"? -> <statement>".

Output plain text.
"""


def _clean_transcription(text: str) -> str:
    """
    Remove accidental markdown fences and unnecessary
    whitespace while preserving the actual note content.
    """

    text = text.strip()

    text = re.sub(
        r"^```(?:text|markdown)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def transcribe_image(
    image_path: str | Path,
) -> str:
    """
    Transcribe text from a note image using Ollama vision.
    """

    if groq_available():
        try:
            transcription = _clean_transcription(
                call_groq_vision(TRANSCRIPTION_PROMPT, str(image_path))
            )
            if transcription:
                return transcription
        except LLMCallError as exc:
            print(f"[transcription] Groq vision failed, using local model: {exc}")

    try:
        response = generate_vision_response(
            image_path=image_path,
            prompt=TRANSCRIPTION_PROMPT,
            temperature=0.0,
            num_ctx=4096,
            num_predict=4096,
        )

    except VisionClientError as exc:
        raise TranscriptionError(
            f"Failed to transcribe image: {exc}"
        ) from exc

    transcription = _clean_transcription(
        response
    )

    if not transcription:
        raise TranscriptionError(
            "Vision model returned empty transcription."
        )

    return transcription