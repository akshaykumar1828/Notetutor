from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import io
import os
import time
import wave

from piper import PiperVoice

import app.common.llm_client  # noqa: F401  (loads .env)
from app.common.paths import work_dir


class TTSError(RuntimeError):
    """Raised when text-to-speech generation fails."""


VOICE_MODEL = Path(
    "models/tts/en_US-lessac-medium.onnx"
)

OUTPUT_DIR = work_dir("audio")


def generate_speech(
    text: str,
    scene_id: str,
) -> tuple[str, float]:

    # ---------------------------------------------------------
    # Validate input
    # ---------------------------------------------------------

    if not text or not text.strip():
        raise TTSError(
            "Cannot generate speech from empty text."
        )

    if not VOICE_MODEL.exists():
        raise TTSError(
            "Piper voice model not found:\n"
            f"{VOICE_MODEL}"
        )

    # ---------------------------------------------------------
    # Prepare output
    # ---------------------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        OUTPUT_DIR /
        f"{scene_id}.wav"
    )

    # Remove previous broken output
    if output_path.exists():
        output_path.unlink()

    # ---------------------------------------------------------
    # Load Piper model
    # ---------------------------------------------------------

    voice = _load_voice()

    # ---------------------------------------------------------
    # Generate WAV
    # ---------------------------------------------------------

    try:

        with wave.open(
            str(output_path),
            "wb",
        ) as wav_file:

            voice.synthesize_wav(
                text,
                wav_file,
            )

    except Exception as exc:

        raise TTSError(
            f"Failed to generate speech "
            f"for {scene_id}: {exc}"
        ) from exc

    # ---------------------------------------------------------
    # Verify output
    # ---------------------------------------------------------

    if not output_path.exists():
        raise TTSError(
            "Piper did not create the output file."
        )

    file_size = output_path.stat().st_size

    if file_size == 0:
        raise TTSError(
            "Piper created an empty WAV file."
        )

    # ---------------------------------------------------------
    # Calculate duration
    # ---------------------------------------------------------

    try:

        with wave.open(
            str(output_path),
            "rb",
        ) as wav_file:

            frames = wav_file.getnframes()
            rate = wav_file.getframerate()

            if rate <= 0:
                raise TTSError(
                    "Invalid WAV sample rate."
                )

            duration = frames / float(rate)

    except TTSError:
        raise

    except Exception as exc:

        raise TTSError(
            f"Could not determine audio duration: {exc}"
        ) from exc

    # ---------------------------------------------------------
    # Final validation
    # ---------------------------------------------------------

    if duration <= 0:
        raise TTSError(
            "Generated audio has zero duration."
        )

    return (
        str(output_path),
        duration,
    )


@lru_cache(maxsize=1)
def _load_voice() -> PiperVoice:
    if not VOICE_MODEL.exists():
        raise TTSError(f"Piper voice model not found: {VOICE_MODEL}")
    try:
        return PiperVoice.load(str(VOICE_MODEL))
    except Exception as exc:
        raise TTSError(f"Could not load Piper voice model: {exc}") from exc


@dataclass
class NarrationAudio:
    path: str
    duration: float
    # (start, end) seconds of each sentence inside the scene audio
    spans: list[tuple[float, float]]


def _synthesize_frames(voice: PiperVoice, text: str) -> tuple[bytes, int, int, int]:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    buffer.seek(0)
    with wave.open(buffer, "rb") as wav_file:
        return (
            wav_file.readframes(wav_file.getnframes()),
            wav_file.getframerate(),
            wav_file.getsampwidth(),
            wav_file.getnchannels(),
        )


# Natural neural voice via Groq (free tier, needs a one-time terms
# acceptance in the Groq console). Falls back to local Piper.
# auto = Kokoro (natural, local, unlimited) if its model files exist,
# otherwise Piper. "orpheus" (Groq) must be chosen explicitly: its free
# tier only allows a few minutes of speech per day.
TTS_ENGINE = os.environ.get("NOTETUTOR_TTS", "auto")  # auto | kokoro | piper | orpheus
ORPHEUS_MODEL = "canopylabs/orpheus-v1-english"
ORPHEUS_VOICE = os.environ.get("NOTETUTOR_ORPHEUS_VOICE", "troy")
KOKORO_DIR = Path("models/tts/kokoro")
KOKORO_VOICE = os.environ.get("NOTETUTOR_TTS_VOICE", "af_heart")
# A touch slower than default: clearer for learners without sounding slow.
SPEECH_SPEED = float(os.environ.get("NOTETUTOR_SPEECH_SPEED", "0.95"))
# Every sentence is brought to the same loudness (RMS, dBFS).
TARGET_RMS_DB = -18.0


def _normalise(frames: bytes, width: int) -> bytes:
    """Equal loudness for every sentence, with a peak limit to avoid clipping."""
    import numpy as np

    if width != 2 or not frames:
        return frames
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(pcm ** 2))) or 1e-9
    gain = 10 ** (TARGET_RMS_DB / 20) / rms
    gain = min(gain, 0.95 / max(float(np.abs(pcm).max()), 1e-9))
    return (np.clip(pcm * gain, -1, 1) * 32767).astype(np.int16).tobytes()
MAX_TTS_WAIT_SECONDS = 20.0


def _orpheus_frames(text: str) -> tuple[bytes, int, int, int]:
    import httpx

    from app.common.llm_client import GROQ_API_URL

    url = GROQ_API_URL.replace("chat/completions", "audio/speech")
    for attempt in range(4):
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {os.environ.get('GROQ_API_KEY', '')}"},
            json={"model": ORPHEUS_MODEL, "voice": ORPHEUS_VOICE,
                  "input": text, "response_format": "wav"},
            timeout=120.0,
        )
        if response.status_code == 429 and attempt < 3:
            wait = float(response.headers.get("retry-after") or 5 * (attempt + 1))
            if wait > MAX_TTS_WAIT_SECONDS:
                raise TTSError(f"Orpheus rate limit: retry in {wait:.0f}s")
            time.sleep(wait)
            continue
        if response.status_code != 200:
            raise TTSError(f"Orpheus TTS failed ({response.status_code}): {response.text[:300]}")
        with wave.open(io.BytesIO(response.content), "rb") as wav_file:
            return (wav_file.readframes(wav_file.getnframes()), wav_file.getframerate(),
                    wav_file.getsampwidth(), wav_file.getnchannels())
    raise TTSError("Orpheus TTS rate limit persisted.")


@lru_cache(maxsize=1)
def active_engine() -> str:
    """Chosen once per run so the whole lesson uses one voice."""
    if TTS_ENGINE == "orpheus":
        _orpheus_frames("Hello.")
        print(f"[tts] Using Orpheus voice '{ORPHEUS_VOICE}'.")
        return "orpheus"
    if TTS_ENGINE in ("auto", "kokoro"):
        try:
            _load_kokoro()
            print(f"[tts] Using Kokoro voice '{KOKORO_VOICE}'.")
            return "kokoro"
        except Exception as exc:
            if TTS_ENGINE == "kokoro":
                raise
            print(f"[tts] Kokoro unavailable, using Piper: {str(exc)[:200]}")
    return "piper"


@lru_cache(maxsize=1)
def _load_kokoro():
    from kokoro_onnx import Kokoro

    model = KOKORO_DIR / "kokoro-v1.0.onnx"
    voices = KOKORO_DIR / "voices-v1.0.bin"
    if not model.exists() or not voices.exists():
        raise TTSError(f"Kokoro model files not found in {KOKORO_DIR}")
    return Kokoro(str(model), str(voices))


def _kokoro_frames(text: str) -> tuple[bytes, int, int, int]:
    import numpy as np

    samples, rate = _load_kokoro().create(text, voice=KOKORO_VOICE, speed=SPEECH_SPEED, lang="en-us")
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    return pcm.tobytes(), rate, 2, 1


def synthesize_narration(
    sentences: list[str],
    scene_id: str,
    lead_in: float = 1.5,
    gap: float = 0.35,
    tail: float = 0.9,
    question_pause: float = 1.2,
) -> NarrationAudio:
    """
    Synthesises each sentence separately and joins them with short
    pauses into one WAV, returning the exact time span of every
    sentence. The visual timeline is built from these spans, so
    what is shown always matches what is being said.
    """

    sentences = [s.strip() for s in sentences if s and s.strip()]
    if not sentences:
        raise TTSError("Cannot generate speech from empty narration.")

    engine = active_engine()
    voice = _load_voice() if engine == "piper" else None
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{scene_id}.wav"

    chunks = []
    rate = width = channels = None
    for sentence in sentences:
        try:
            if engine == "orpheus":
                frames, r, w, c = _orpheus_frames(sentence)
            elif engine == "kokoro":
                frames, r, w, c = _kokoro_frames(sentence)
            else:
                frames, r, w, c = _synthesize_frames(voice, sentence)
        except Exception as exc:
            raise TTSError(f"Failed to synthesise '{sentence[:60]}': {exc}") from exc
        if not frames:
            raise TTSError(f"Piper returned no audio for '{sentence[:60]}'")
        rate, width, channels = r, w, c
        chunks.append(_normalise(frames, w))

    bytes_per_second = rate * width * channels

    def silence(seconds: float) -> bytes:
        return bytes(int(seconds * rate) * width * channels)

    spans = []
    cursor = lead_in
    audio = bytearray(silence(lead_in))
    for index, frames in enumerate(chunks):
        length = len(frames) / bytes_per_second
        spans.append((cursor, cursor + length))
        audio += frames
        cursor += length
        pause = tail if index == len(chunks) - 1 else gap
        if sentences[index].rstrip().endswith("?"):
            # Give the viewer a moment to think about a question.
            pause = max(pause, question_pause)
        audio += silence(pause)
        cursor += pause

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(width)
        wav_file.setframerate(rate)
        wav_file.writeframes(bytes(audio))

    return NarrationAudio(str(output_path), len(audio) / bytes_per_second, spans)
