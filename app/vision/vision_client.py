import base64
from pathlib import Path

import httpx


OLLAMA_URL = "http://localhost:11434/api/generate"

# Gemma 3 supports image input.
MODEL_NAME = "gemma3:4b-it-q4_K_M"


class VisionClientError(RuntimeError):
    """Raised when communication with the vision model fails."""


def _encode_image(image_path: str | Path) -> str:
    """
    Convert an image into base64 for Ollama.
    """

    path = Path(image_path)

    if not path.exists():
        raise VisionClientError(
            f"Image not found: {path}"
        )

    try:
        with path.open("rb") as image_file:
            encoded = base64.b64encode(
                image_file.read()
            ).decode("utf-8")

    except Exception as exc:
        raise VisionClientError(
            f"Could not read image: {exc}"
        ) from exc

    return encoded


def generate_vision_response(
    image_path: str | Path,
    prompt: str,
    *,
    temperature: float = 0.1,
    num_ctx: int = 4096,
    num_predict: int = 2048,
) -> str:
    """
    Send an image + prompt to Ollama's vision model.
    """

    if not prompt or not prompt.strip():
        raise VisionClientError(
            "Vision prompt cannot be empty."
        )

    image_base64 = _encode_image(
        image_path
    )

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "images": [
            image_base64
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
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
        raise VisionClientError(
            f"Could not communicate with Ollama: {exc}"
        ) from exc

    try:
        data = response.json()

    except ValueError as exc:
        raise VisionClientError(
            "Ollama returned invalid JSON."
        ) from exc

    result = data.get("response")

    if not result:
        raise VisionClientError(
            "Vision model returned an empty response."
        )

    return result.strip()