"""
Optional Groq-backed text generation. If GROQ_API_KEY is set (in
your environment or .env file), text-generation calls route through
Groq's hosted Llama 3.3 70B instead of the local quantized gemma3:4b
via Ollama. Falls back to Ollama automatically if the key isn't set
or the request fails, so nothing breaks if you don't use Groq.

Why this helps: most of the JSON-malformation / instruction-following
bugs we've hit trace back to gemma3:4b being a small, 4-bit quantized
model. A much larger hosted model is simply more reliable at
following structured output instructions, with no code-level
workarounds needed.

Groq's API is OpenAI-compatible, so this uses plain httpx rather than
pulling in a new SDK dependency.
"""

import os
import time

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# NOTE: llama-3.3-70b-versatile was deprecated by Groq and fully
# shut down August 16, 2026. Using their recommended replacement.
# If this ever 404s again, check https://console.groq.com/docs/models
# for the current model list — Groq's free-tier lineup changes
# fairly often.
GROQ_MODEL = "openai/gpt-oss-120b"

# Free-tier rate limit is 30 requests/minute. Our pipeline can fire
# several calls in quick succession (planner, generator + retry,
# verifier x2 per scene), so 429s are expected under normal use, not
# a real failure — worth a short retry instead of immediately
# giving up and falling back to the local model.
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 5.0

# Longer waits (daily quota exhausted) fall back to local Ollama instead.
MAX_RATE_LIMIT_WAIT_SECONDS = 60.0


class LLMCallError(RuntimeError):
    """Raised when both Groq and the Ollama fallback fail."""


class GroqRateLimited(LLMCallError):
    """A Groq model is rate limited for `delay` seconds."""

    def __init__(self, message: str, delay: float):
        super().__init__(message)
        self.delay = delay


# When every Groq model is busy, waiting this long for the soonest one is
# better than falling back to the much weaker local model.
MAX_CHAIN_WAIT_SECONDS = 300.0

# The pipeline turns this off unless --allow-local is given: a lesson built
# by the small local model is too inaccurate to be worth producing.
ALLOW_LOCAL_FALLBACK = True


class OnlineModelsUnavailable(RuntimeError):
    """Groq is configured but unusable, and local fallback is not allowed.

    Deliberately NOT an LLMCallError, so stage-level fallbacks to Ollama
    do not catch it and the pipeline stops with a clear message.
    """


def _no_fallback(errors: str) -> None:
    if (os.environ.get("GROQ_API_KEY") or os.environ.get("GEMINI_API_KEY")) and not ALLOW_LOCAL_FALLBACK:
        raise OnlineModelsUnavailable(
            "The online models (Groq and Gemini free tiers) did not answer, even after "
            "waiting several minutes. Either today's free quota is used up or the "
            "providers are overloaded. Try again later (answers already received are "
            "cached, so a retry continues quickly), or re-run with --allow-local to "
            "accept lower-quality local models.\n"
            f"Details: {errors[:400]}"
        )


def groq_available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


# Free-tier quotas are per model, so when the strongest model is out of
# quota the next one is tried before falling back to local Ollama.
GROQ_TEXT_MODELS = [
    m.strip() for m in os.environ.get(
        "NOTETUTOR_TEXT_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b"
    ).split(",") if m.strip()
]


# ---------------------------------------------------------------------
# Usage statistics and an exact-request answer cache
# ---------------------------------------------------------------------

STATS = {"requests": 0, "request_seconds": 0.0, "wait_seconds": 0.0,
         "tokens": 0, "cache_hits": 0}

# Identical requests (same prompt, same inputs) reuse the earlier answer:
# re-running the same material costs no quota. Disable with --fresh.
CACHE_ENABLED = os.environ.get("NOTETUTOR_FRESH", "") == ""
LLM_CACHE_DIR = os.path.join("data", "cache", "llm")


def _sleep(seconds: float) -> None:
    STATS["wait_seconds"] += seconds
    time.sleep(seconds)


def _cache_path(*parts) -> str:
    import hashlib

    digest = hashlib.sha1()
    for part in parts:
        digest.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        digest.update(b"\x00")
    return os.path.join(LLM_CACHE_DIR, digest.hexdigest() + ".txt")


def _cached(path: str) -> str | None:
    if CACHE_ENABLED and os.path.exists(path):
        STATS["cache_hits"] += 1
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    return None


def _store(path: str, text: str) -> str:
    if CACHE_ENABLED and text and text.strip():
        os.makedirs(LLM_CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text


# Light, mechanical tasks (terminology, after-video review) go to the
# smaller model first. It has its own per-minute limit, so the main
# model's limit is left for the tasks that write and check lesson content.
GROQ_LIGHT_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]


def call_groq(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = True,
    light: bool = False,
) -> str:
    path = _cache_path("text", prompt, json_mode, max_tokens)
    cached = _cached(path)
    if cached is not None:
        return cached
    models = GROQ_LIGHT_MODELS if light else GROQ_TEXT_MODELS
    return _store(path, _call_text_chain(prompt, temperature, max_tokens, json_mode, models))


def _call_text_chain(prompt, temperature, max_tokens, json_mode, models) -> str:
    errors = []
    waits = []
    for model in models:
        try:
            return _call_groq_model(model, prompt, temperature, max_tokens, json_mode)
        except GroqRateLimited as exc:
            errors.append(f"{model}: {exc}")
            waits.append((exc.delay, model))
        except LLMCallError as exc:
            errors.append(f"{model}: {exc}")

    # Busy Groq: ask Gemini right away rather than sleeping; only if
    # Gemini also fails is it worth waiting for Groq's limit to reset.
    if gemini_available():
        try:
            return call_gemini(prompt, max_tokens=max(max_tokens, 2048),
                               json_mode=json_mode, temperature=temperature)
        except LLMCallError as exc:
            errors.append(str(exc))
    # Per-minute limits (long documents send many requests) clear within a
    # minute, so keep waiting for them instead of failing the whole lesson.
    waited = 0.0
    while waits and min(waits)[0] <= MAX_CHAIN_WAIT_SECONDS - waited:
        delay, model = min(waits)
        print(f"[llm_client] All providers busy; waiting {delay:.0f}s for {model}...")
        _sleep(delay + 1)
        waited += delay + 1
        waits = []
        try:
            return _call_groq_model(model, prompt, temperature, max_tokens, json_mode)
        except GroqRateLimited as exc:
            waits.append((exc.delay, model))
            errors.append(f"{model} after waiting: {exc}")
        except LLMCallError as exc:
            errors.append(f"{model} after waiting: {exc}")
    _no_fallback(" | ".join(errors))
    raise LLMCallError(" | ".join(errors))


def _call_groq_model(
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> str:
    """
    Calls Groq's chat completions endpoint. Retries automatically on
    429 (rate limit) with a short backoff, since that's an expected,
    transient condition under free-tier limits — not a real failure.
    Raises LLMCallError only after retries are exhausted or on a
    genuine (non-429) failure — callers should catch this and fall
    back to Ollama.
    """

    api_key = os.environ.get("GROQ_API_KEY")

    if not api_key:
        raise LLMCallError("GROQ_API_KEY is not set.")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if model.startswith("openai/gpt-oss"):
        # Hidden reasoning tokens count against the daily quota; these
        # structured-writing tasks do not need long deliberation.
        payload["reasoning_effort"] = "low"

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_exc: Exception | None = None

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):

        try:
            started = time.time()
            response = httpx.post(
                GROQ_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=60.0,
            )
            STATS["requests"] += 1
            STATS["request_seconds"] += time.time() - started
            response.raise_for_status()

            data = response.json()
            STATS["tokens"] += data.get("usage", {}).get("total_tokens", 0)
            return data["choices"][0]["message"]["content"]

        except httpx.HTTPStatusError as exc:

            if exc.response.status_code == 429:

                if attempt >= MAX_RATE_LIMIT_RETRIES:
                    last_exc = exc
                    break

                retry_after = exc.response.headers.get("retry-after")

                try:
                    delay = float(retry_after) if retry_after else (
                        DEFAULT_RETRY_DELAY_SECONDS * (attempt + 1)
                    )
                except ValueError:
                    delay = DEFAULT_RETRY_DELAY_SECONDS * (attempt + 1)

                if delay > MAX_RATE_LIMIT_WAIT_SECONDS:
                    raise GroqRateLimited(
                        f"Groq rate limit: retry in {delay:.0f}s is too long; "
                        f"using the local model instead.",
                        delay,
                    )

                print(
                    f"[llm_client] Groq rate limit hit (429). "
                    f"Retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})..."
                )
                _sleep(delay)
                continue

            # Non-429 HTTP error — not worth retrying.
            raise LLMCallError(
                f"Groq request failed: {exc} :: {exc.response.text[:300]}"
            ) from exc

        except httpx.HTTPError as exc:
            raise LLMCallError(f"Groq request failed: {exc}") from exc

        except (KeyError, IndexError, ValueError) as exc:
            raise LLMCallError(
                f"Unexpected Groq response shape: {exc}"
            ) from exc

    # Still busy after the short retries: a per-minute limit, which resets
    # within a minute. Report it as a wait so the chain can retry later.
    raise GroqRateLimited(
        f"Groq rate limit persisted after {MAX_RATE_LIMIT_RETRIES} retries: {last_exc}",
        MAX_RATE_LIMIT_WAIT_SECONDS,
    )

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma3:4b-it-q4_K_M"


def generate_json(
    prompt: str,
    schema: dict | None = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    label: str = "llm",
    light: bool = False,
) -> dict:
    """
    Returns a parsed JSON object. Uses Groq when GROQ_API_KEY is set
    (free tier), otherwise — or if Groq fails — the local Ollama model,
    constrained by `schema` when given.
    """

    raw = None

    if groq_available():
        try:
            raw = call_groq(prompt, temperature=temperature, max_tokens=max_tokens, light=light)
        except LLMCallError as exc:
            print(f"[{label}] Groq failed, using local Ollama: {exc}")

    if raw is None:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": schema or "json",
            "options": {
                "temperature": temperature,
                "num_ctx": 8192,
                "num_predict": max_tokens,
            },
        }
        try:
            response = httpx.post(OLLAMA_URL, json=payload, timeout=300.0)
            response.raise_for_status()
            raw = response.json().get("response")
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMCallError(f"Ollama request failed: {exc}") from exc

    if not raw:
        raise LLMCallError("Model returned an empty response.")

    from json_repair import repair_json

    data = repair_json(raw, return_objects=True)
    if not isinstance(data, dict) or not data:
        raise LLMCallError(f"Model did not return a JSON object:\n{raw[:500]}")
    return data


GROQ_VISION_MODEL = os.environ.get("NOTETUTOR_VISION_MODEL", "qwen/qwen3.8-27b")


def call_groq_vision(
    prompt: str,
    image_path: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    with open(image_path, "rb") as handle:
        path = _cache_path("vision", prompt, handle.read(), json_mode)
    cached = _cached(path)
    if cached is not None:
        return cached
    return _store(path, _call_groq_vision_uncached(
        prompt, image_path, temperature, max_tokens, json_mode))


def _call_groq_vision_uncached(
    prompt: str,
    image_path: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    """Image + prompt to Groq's vision model (free tier). Retries 429s."""

    import base64
    import mimetypes

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise LLMCallError("GROQ_API_KEY is not set.")

    mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    with open(image_path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode()

    payload = {
        "model": GROQ_VISION_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            response = httpx.post(
                GROQ_API_URL, json=payload,
                headers={"Authorization": f"Bearer {api_key}"}, timeout=120.0,
            )
            if response.status_code in (429, 500, 502, 503) and attempt < MAX_RATE_LIMIT_RETRIES:
                try:
                    wait = float(response.headers.get("retry-after") or 0)
                except ValueError:
                    wait = 0.0
                wait = min(max(wait, DEFAULT_RETRY_DELAY_SECONDS * (attempt + 1)), 60.0)
                print(f"[llm_client] Groq vision busy ({response.status_code}); retrying in {wait:.0f}s...")
                _sleep(wait)
                continue
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return _vision_backup(prompt, image_path, max_tokens, json_mode, str(exc))
    return _vision_backup(prompt, image_path, max_tokens, json_mode, "vision rate limit persisted")


def _vision_backup(prompt, image_path, max_tokens, json_mode, groq_error: str) -> str:
    """Groq vision failed: try Gemini, then (only if allowed) the local model."""
    if gemini_available():
        try:
            return call_gemini(prompt, max_tokens=max(max_tokens, 2048), json_mode=json_mode,
                               temperature=0.0, image_path=str(image_path))
        except LLMCallError as exc:
            groq_error += f" | {exc}"
    _no_fallback(groq_error)
    raise LLMCallError(f"Groq vision request failed: {groq_error}")


# ---------------------------------------------------------------------
# Google Gemini (free tier) as a second provider. Used only when every
# Groq model has failed, so its quota is spent only when needed.
# ---------------------------------------------------------------------

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# "-latest" aliases keep working when Google retires a model version.
GEMINI_MODELS = [
    m.strip() for m in os.environ.get(
        "NOTETUTOR_GEMINI_MODELS", "gemini-flash-latest,gemini-flash-lite-latest"
    ).split(",") if m.strip()
]


def gemini_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def call_gemini(
    prompt: str,
    max_tokens: int = 4096,
    json_mode: bool = True,
    temperature: float = 0.2,
    image_path: str | None = None,
) -> str:
    """Text (or image + text) request to Gemini; tries each model in turn."""
    import base64
    import mimetypes

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMCallError("GEMINI_API_KEY is not set.")

    parts = [{"text": prompt}]
    if image_path:
        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        with open(image_path, "rb") as handle:
            parts.append({"inline_data": {"mime_type": mime,
                                          "data": base64.b64encode(handle.read()).decode()}})
    config = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_mode:
        config["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": parts}], "generationConfig": config}

    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                response = httpx.post(
                    GEMINI_API_URL.format(model=model), params={"key": key},
                    json=payload, timeout=180.0,
                )
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {exc}")
                break
            if response.status_code in (429, 500, 503) and attempt < 2:
                _sleep(10 * (attempt + 1))
                continue
            if response.status_code != 200:
                errors.append(f"{model}: HTTP {response.status_code} {response.text[:200]}")
                break
            try:
                candidate = response.json()["candidates"][0]
                text = "".join(p.get("text", "") for p in candidate["content"]["parts"])
            except (KeyError, IndexError, ValueError) as exc:
                errors.append(f"{model}: unexpected response {exc}")
                break
            if text.strip():
                print(f"[llm_client] Answered by Gemini ({model}).")
                return text
            errors.append(f"{model}: empty response")
            break
    raise LLMCallError("Gemini failed: " + " | ".join(errors))
