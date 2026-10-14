"""
Transcription accuracy: character and word error rate (CER / WER) of the
page reading against a hand-made reference transcript.

    python -m evaluation.eval_transcription <image> <reference.txt> [--fresh]

Uses the cached transcription from the last pipeline run unless --fresh.
Text is normalised (case, punctuation, arrows, whitespace) before scoring.
The "links removed" score also drops the "<term> ->" prefixes the
transcriber adds on purpose to record which term an arrow points from.
"""

import re
import sys
from pathlib import Path


def normalise(text: str) -> str:
    text = text.lower().replace("→", " ").replace("->", " ")
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def edit_distance(a, b) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def scores(hypothesis: str, reference: str) -> tuple[float, float]:
    h, r = normalise(hypothesis), normalise(reference)
    cer = edit_distance(h, r) / max(len(r), 1)
    wer = edit_distance(h.split(), r.split()) / max(len(r.split()), 1)
    return cer, wer


def main(image: str, reference_path: str, fresh: bool = False) -> str:
    from app.common import llm_client
    from app.pipeline import lesson_video

    llm_client.ALLOW_LOCAL_FALLBACK = False
    if fresh:
        from app.vision.transcription_client import transcribe_note
        hypothesis = transcribe_note(image).transcription
    else:
        hypothesis = lesson_video._transcribe_image(image)
    reference = Path(reference_path).read_text(encoding="utf-8")
    without_links = re.sub(r"^[^\n>]{1,40}->\s*", "", hypothesis, flags=re.MULTILINE)

    rows = ["| Transcription | CER | WER |", "|---|---|---|"]
    for name, text in (("as produced", hypothesis), ("links removed", without_links)):
        cer, wer = scores(text, reference)
        rows.append(f"| {name} | {100 * cer:.1f}% | {100 * wer:.1f}% |")
    table = "\n".join(rows)
    print(table)
    return table


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--fresh" in sys.argv)
