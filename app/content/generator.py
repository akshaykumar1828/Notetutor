import json
import re

import httpx

from app.common.llm_client import (
    LLMCallError,
    call_groq,
    groq_available,
)
from app.content.prompts import CONTENT_GENERATION_PROMPT
from app.content.schemas import SceneContent


OLLAMA_URL = "http://localhost:11434/api/generate"

MODEL_NAME = "gemma3:4b-it-q4_K_M"


class ContentGenerationError(RuntimeError):
    """Raised when scene content generation fails."""


def _strip_markdown_emphasis(text: str | None) -> str | None:
    """
    Remove markdown emphasis markers (*word*, **word**) that Gemma
    sometimes adds despite instructions not to. This text is both
    displayed as video captions and fed directly to TTS — asterisks
    must never reach either.
    """

    if text is None:
        return None

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)

    return text


def _clean_scene_content(content: SceneContent) -> SceneContent:

    content.explanation = _strip_markdown_emphasis(
        content.explanation
    ) or content.explanation

    content.key_takeaways = [
        _strip_markdown_emphasis(item) or item
        for item in content.key_takeaways
    ]

    content.example = _strip_markdown_emphasis(
        content.example
    )

    content.misconception_correction = _strip_markdown_emphasis(
        content.misconception_correction
    )

    return content


def _min_takeaways_for(duration_weight: str) -> int:
    """
    Minimum key_takeaways count per duration_weight. Mirrors the
    guidance in CONTENT_GENERATION_PROMPT, but enforced in code
    because gemma3:4b (a small, quantized model) does not reliably
    follow numeric constraints from prompt text alone.
    """

    return {
        "brief": 2,
        "standard": 4,
        "deep_dive": 6,
    }.get(duration_weight, 4)


def _max_explanation_words_for(duration_weight: str) -> int:
    """
    Caps explanation length so scene DURATION (driven by TTS audio
    length, driven by explanation length) actually matches how much
    on-screen content exists. An overly long explanation with only
    3-4 key_takeaways creates a long, mostly-static scene — visually
    "stuck" even though the bullets built up fine at the start.
    """

    return {
        "brief": 60,
        "standard": 95,
        "deep_dive": 140,
    }.get(duration_weight, 95)


def _truncate_to_word_limit(text: str, max_words: int) -> str:
    """
    Hard truncation, used as a last resort when the model ignores
    the word-count instruction even after a retry. Cuts at the last
    complete sentence that fits within max_words (with a little
    headroom), rather than chopping mid-sentence. If no sentence
    boundary exists within range, falls back to a plain word cut.

    This exists because logging a warning and moving on (the
    previous behavior) let scenes run 2-4x longer than intended
    whenever the model simply didn't comply — a real duration
    guarantee requires actually enforcing the limit, not just
    detecting the violation.
    """

    words = text.split()

    if len(words) <= max_words:
        return text

    # Allow a little headroom so we don't cut off a sentence that's
    # only a word or two over the limit.
    headroom = int(max_words * 1.15)
    candidate = " ".join(words[:headroom])

    # Try to end on a real sentence boundary within the candidate.
    sentence_ends = [
        candidate.rfind(". "),
        candidate.rfind("! "),
        candidate.rfind("? "),
    ]

    best_end = max(sentence_ends)

    if best_end > 0:
        return candidate[: best_end + 1].strip()

    # No clean sentence boundary — fall back to a plain word cut at
    # the actual target (not the headroom) and add a period if
    # needed so it doesn't look like it stops mid-thought.
    truncated = " ".join(words[:max_words]).rstrip(",;: ")

    if not truncated.endswith((".", "!", "?")):
        truncated += "."

    return truncated


def generate_scene_content(
    scene,
    dominant_topic: str,
    audience: str,
    verified_knowledge: str = "",
    target_words: int | None = None,
) -> SceneContent:

    scene_json = scene.model_dump_json(
        indent=2
    )

    duration_weight = getattr(
        scene, "duration_weight", "standard"
    )

    minimum_takeaways = _min_takeaways_for(
        duration_weight
    )

    length_instruction = (
        f"LENGTH: the explanation must be about {target_words} words "
        f"(between {int(target_words * 0.8)} and {int(target_words * 1.15)}). "
        "This keeps the whole video a sensible length."
        if target_words else ""
    )

    def _build_prompt(extra_instruction: str = "") -> str:
        return f"""
{CONTENT_GENERATION_PROMPT}

======================================================================
DOMINANT TOPIC
======================================================================

{dominant_topic}

======================================================================
AUDIENCE
======================================================================

{audience}

======================================================================
VERIFIED KNOWLEDGE
======================================================================

{verified_knowledge}

======================================================================
SCENE TO TEACH
======================================================================

{scene_json}

{length_instruction}
{extra_instruction}

Now generate the educational content for this scene.
"""

    def _call_ollama(prompt: str) -> SceneContent:

        raw_response = None

        # Prefer Groq (much larger hosted model) if configured — the
        # local 4B model has repeatedly fabricated distinctions not
        # actually present in the source notes (e.g. inventing that
        # one named example is "more agentic" than another when the
        # notes list them together, undifferentiated). A stronger
        # model is far less prone to this kind of confident
        # invention. Falls back to local Ollama automatically if
        # Groq isn't configured or the call fails.
        if groq_available():

            try:
                print(
                    "[generator] Using Groq "
                    "(openai/gpt-oss-120b) instead of local "
                    "Ollama..."
                )
                raw_response = call_groq(
                    prompt, temperature=0.2, max_tokens=2048
                )

            except LLMCallError as exc:
                print(
                    f"[generator] Groq call failed, falling back "
                    f"to local Ollama: {exc}"
                )
                raw_response = None

        if raw_response is None:

            payload = {
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.2,
                    "num_ctx": 4096,
                    "num_batch": 64,
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
                raise ContentGenerationError(
                    f"Could not communicate with Ollama: {exc}"
                ) from exc

            data = response.json()

            raw_response = data.get("response")

        if not raw_response:
            raise ContentGenerationError(
                "Model returned an empty scene content response."
            )

        raw_response = raw_response.strip()

        if raw_response.startswith("```"):
            raw_response = raw_response.replace(
                "```json", "", 1,
            )
            raw_response = raw_response.replace(
                "```", "",
            ).strip()

        try:
            parsed = json.loads(raw_response)
            content = SceneContent.model_validate(parsed)
            return _clean_scene_content(content)

        except Exception as exc:
            raise ContentGenerationError(
                "Invalid scene content returned by Gemma.\n"
                f"Raw response:\n{raw_response}\n\n"
                f"Validation error: {exc}"
            ) from exc

    # -----------------------------------------------------------
    # First attempt
    # -----------------------------------------------------------

    content = _call_ollama(_build_prompt())

    # -----------------------------------------------------------
    # Enforce BOTH minimum key_takeaways count AND maximum
    # explanation length with ONE combined retry if Gemma got
    # either wrong. Small quantized models often ignore numeric
    # constraints stated only in prose — checking and correcting in
    # code is what actually makes these limits hold.
    # -----------------------------------------------------------

    max_words = target_words or _max_explanation_words_for(duration_weight)
    explanation_word_count = len(content.explanation.split())

    # Takeaways are no longer shown directly (the visual planner builds
    # the visual), so only a badly overlong narration is worth a retry.
    too_few_takeaways = len(content.key_takeaways) < 1
    too_long_explanation = explanation_word_count > int(max_words * 1.6)

    if too_few_takeaways or too_long_explanation:

        problems = []

        if too_few_takeaways:
            problems.append(
                f"only {len(content.key_takeaways)} key_takeaways "
                f"(need at least {minimum_takeaways})"
            )

        if too_long_explanation:
            problems.append(
                f"explanation is {explanation_word_count} words "
                f"(should be roughly {max_words} words or fewer "
                f"for a '{duration_weight}' scene — this scene's "
                f"video will run far longer than its on-screen "
                f"content justifies)"
            )

        print(
            f"[generator] Scene issues: {'; '.join(problems)}. "
            f"Retrying with an explicit correction..."
        )

        retry_instruction = f"""
======================================================================
CORRECTION REQUIRED
======================================================================

Your previous attempt has the following problem(s): {'; '.join(problems)}.

Provide a complete, corrected response that fixes ALL of the above:

- At least {minimum_takeaways} distinct, substantive key_takeaways
  (do not split existing ones into fragments — add genuinely
  distinct points the explanation supports).
- An explanation of roughly {max_words} words or fewer for this
  "{duration_weight}" scene. Say what's necessary, but do not pad
  with restated points or unnecessary elaboration beyond that length.

Previous explanation (for reference, to trim/adjust, not to keep
as-is if it's too long):

{content.explanation}

Previous key_takeaways (for reference):

{json.dumps(content.key_takeaways, indent=2)}
"""

        try:
            retried = _call_ollama(
                _build_prompt(retry_instruction)
            )

            retried_word_count = len(retried.explanation.split())

            # Only adopt the retry if it's actually better on at
            # least one axis and not worse on the other.
            retried_is_better = (
                len(retried.key_takeaways) >= len(content.key_takeaways)
                and retried_word_count <= max(
                    explanation_word_count, int(max_words * 1.3)
                )
            )

            if retried_is_better:
                content = retried

        except ContentGenerationError:
            # Keep the original content rather than failing the
            # whole scene over a retry that itself errored.
            pass

        final_word_count = len(content.explanation.split())

        if len(content.key_takeaways) < minimum_takeaways:
            print(
                f"[generator] Still only "
                f"{len(content.key_takeaways)} key_takeaways after "
                f"retry — proceeding anyway rather than blocking "
                f"the pipeline."
            )

        if final_word_count > int(max_words * 1.3):
            print(
                f"[generator] Explanation still {final_word_count} "
                f"words after retry (target {max_words}) — "
                f"hard-truncating rather than letting this scene "
                f"run far longer than intended."
            )
            content.explanation = _truncate_to_word_limit(
                content.explanation, max_words
            )

    return content