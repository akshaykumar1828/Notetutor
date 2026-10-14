"""
Splits a scene explanation into narration sentences.

Sentences are the unit of synchronisation: each one is synthesised
separately, so its exact start/end time in the scene audio is known and
visual elements can be revealed when their sentence begins.
"""

import re

# Generic English abbreviations that end with a period but do not end a
# sentence. Not subject-specific.
_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "approx", "fig", "eq", "no", "dr", "mr",
    "mrs", "ms", "prof", "st", "cf", "al",
}

_MIN_WORDS = 4

_TYPOGRAPHY = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": " - ",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "­": "", "​": "",
}


def clean_text(text: str) -> str:
    """Normalises typography and strips markdown emphasis markers."""
    text = text or ""
    for src, dst in _TYPOGRAPHY.items():
        text = text.replace(src, dst)
    text = re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`", lambda m: next(g for g in m.groups() if g), text)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", clean_text(text))
    if not text:
        return []

    pieces: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]+[\"')\]]*\s+", text):
        end = match.end()
        before = text[start:match.start()].split()
        last_word = before[-1].lower().rstrip(".") if before else ""
        is_abbreviation = last_word in _ABBREVIATIONS
        is_number = bool(re.search(r"\d$", text[: match.start()])) and text[end:end + 1].isdigit()
        if is_abbreviation or is_number:
            continue
        pieces.append(text[start:end].strip())
        start = end
    if text[start:].strip():
        pieces.append(text[start:].strip())

    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece.split()) < _MIN_WORDS:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    if len(merged) > 1 and len(merged[0].split()) < _MIN_WORDS:
        merged[1] = f"{merged[0]} {merged[1]}"
        merged.pop(0)
    return merged


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    """Lower-cased, lightly stemmed words longer than 2 letters."""
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}
