"""
Locally synthesised background music: a soft ambient pad with a gentle
arpeggio, generated with numpy (no downloads, no licensing issues).

The lesson title seeds the key and progression, so different lessons
get different (but always calm) music. It is mixed well below the
narration by the renderer.
"""

import hashlib
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
CHORD_SECONDS = 4.0

# Chord progressions as scale degrees (0 = tonic) of the major scale.
_PROGRESSIONS = [
    [0, 4, 5, 3],   # I  V  vi IV
    [5, 3, 0, 4],   # vi IV I  V
    [0, 5, 3, 4],   # I  vi IV V
    [3, 0, 4, 5],   # IV I  V  vi
]
_MAJOR = [0, 2, 4, 5, 7, 9, 11]
_KEYS = [0, 2, 3, 5, 7, 8, 10]  # semitone offsets from C


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _chord_notes(root_midi: int, degree: int) -> list[int]:
    notes = []
    for step in (0, 2, 4):
        index = degree + step
        octave, pos = divmod(index, 7)
        notes.append(root_midi + 12 * octave + _MAJOR[pos])
    return notes


def _envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a = min(int(attack * SAMPLE_RATE), n // 2)
    r = min(int(release * SAMPLE_RATE), n // 2)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return env


# Music level while someone speaks, relative to its level in pauses.
DUCK_LEVEL = 0.35


def generate_music(
    duration: float,
    output_path: str | Path,
    seed_text: str = "",
    speech: list[tuple[float, float]] | None = None,
) -> str:
    seed = int(hashlib.md5(seed_text.encode()).hexdigest(), 16)
    progression = _PROGRESSIONS[seed % len(_PROGRESSIONS)]
    root = 48 + _KEYS[(seed // 7) % len(_KEYS)]  # around C3

    total = int((duration + 1) * SAMPLE_RATE)
    left = np.zeros(total)
    right = np.zeros(total)
    chord_len = int(CHORD_SECONDS * SAMPLE_RATE)
    overlap = int(1.0 * SAMPLE_RATE)
    t_chord = np.arange(chord_len + overlap) / SAMPLE_RATE

    start = 0
    bar = 0
    while start < total:
        degree = progression[bar % len(progression)]
        notes = _chord_notes(root, degree)
        n = min(chord_len + overlap, total - start)
        t = t_chord[:n]
        env = _envelope(n, 1.2, 1.4)

        pad_l = np.zeros(n)
        pad_r = np.zeros(n)
        for note in notes + [notes[0] - 12]:
            f = _freq(note)
            # Slight detune between channels for a wide, soft sound.
            pad_l += np.sin(2 * np.pi * f * 0.998 * t) + 0.25 * np.sin(4 * np.pi * f * t)
            pad_r += np.sin(2 * np.pi * f * 1.002 * t) + 0.25 * np.sin(4 * np.pi * f * t)
        left[start:start + n] += pad_l * env * 0.18
        right[start:start + n] += pad_r * env * 0.18

        # Gentle arpeggio one octave up, eighth notes.
        step = int(0.5 * SAMPLE_RATE)
        arp = [notes[0] + 12, notes[1] + 12, notes[2] + 12, notes[1] + 12]
        for k in range(chord_len // step):
            s0 = start + k * step
            m = min(int(1.2 * SAMPLE_RATE), total - s0)
            if m <= 0:
                break
            tt = np.arange(m) / SAMPLE_RATE
            pluck = np.sin(2 * np.pi * _freq(arp[k % 4]) * tt) * np.exp(-tt * 3.5) * 0.12
            pan = 0.35 + 0.3 * (k % 2)
            left[s0:s0 + m] += pluck * (1 - pan)
            right[s0:s0 + m] += pluck * pan

        start += chord_len
        bar += 1

    stereo = np.stack([left, right], axis=1)[: int(duration * SAMPLE_RATE)]
    fade_in = min(int(2 * SAMPLE_RATE), len(stereo) // 2)
    fade_out = min(int(3 * SAMPLE_RATE), len(stereo) // 2)
    stereo[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    stereo[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    stereo /= max(np.abs(stereo).max(), 1e-9)

    if speech:
        # Duck under the voice with short ramps so the change is smooth.
        envelope = np.ones(len(stereo))
        ramp = int(0.25 * SAMPLE_RATE)
        for start, end in speech:
            a = max(int((start - 0.15) * SAMPLE_RATE), 0)
            b = min(int((end + 0.15) * SAMPLE_RATE), len(envelope))
            if b > a:
                envelope[a:b] = np.minimum(envelope[a:b], DUCK_LEVEL)
        # Moving average via cumulative sums: O(n), unlike a direct
        # convolution with a long window.
        padded = np.concatenate([np.full(ramp // 2, envelope[0]), envelope,
                                 np.full(ramp - ramp // 2, envelope[-1])])
        sums = np.cumsum(padded, dtype=np.float64)
        envelope = (sums[ramp:ramp + len(envelope)] - sums[:len(envelope)]) / ramp
        stereo *= envelope[:, None]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes((stereo * 0.9 * 32767).astype(np.int16).tobytes())
    return str(output_path)
