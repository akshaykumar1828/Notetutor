"""
Shared visual theme (colors, fonts, canvas size) for every renderer.

Fonts are resolved from a list of candidates so the same code works on
Windows, macOS and Linux; set NOTETUTOR_FONT_DIR to override.
"""

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
FPS = 24

BACKGROUND = (18, 24, 38)
BACKGROUND_GLOW = (30, 42, 68)
SURFACE = (34, 44, 64)
SURFACE_FOCUS = (48, 62, 92)
CODE_SURFACE = (12, 17, 28)
BORDER = (90, 110, 140)
ACCENT = (120, 170, 255)
FOCUS = (255, 196, 92)
TEXT = (255, 255, 255)
TEXT_MUTED = (170, 184, 208)

# Distinct, readable-on-dark colours for colour-coding elements.
PALETTE = [
    (110, 168, 255),  # blue
    (178, 140, 255),  # violet
    (72, 214, 190),   # teal
    (255, 128, 140),  # coral
    (140, 220, 110),  # green
    (240, 130, 210),  # pink
    (255, 170, 90),   # orange
]

EMOJI_FONT_CANDIDATES = ["seguiemj.ttf", "NotoColorEmoji.ttf", "Apple Color Emoji.ttc"]

_FONT_DIRS = [
    os.environ.get("NOTETUTOR_FONT_DIR", ""),
    r"C:\Windows\Fonts",
    "/System/Library/Fonts/Supplemental",
    "/usr/share/fonts/truetype/dejavu",
]

_FONT_CANDIDATES = {
    "regular": ["segoeui.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf"],
    "semibold": ["seguisb.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
    "mono": ["consola.ttf", "cour.ttf", "Courier New.ttf", "DejaVuSansMono.ttf"],
}


class ThemeError(RuntimeError):
    """Raised when no usable font can be found."""


@lru_cache(maxsize=None)
def _font_path(role: str) -> str:
    for name in _FONT_CANDIDATES[role]:
        for directory in _FONT_DIRS:
            if directory and (Path(directory) / name).exists():
                return str(Path(directory) / name)
    raise ThemeError(
        f"No font found for role '{role}'. Set NOTETUTOR_FONT_DIR to a "
        f"folder containing one of: {_FONT_CANDIDATES[role]}"
    )


@lru_cache(maxsize=256)
def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(role), max(int(size), 6))


def line_step(f: ImageFont.FreeTypeFont) -> float:
    """Vertical distance between consecutive wrapped lines."""
    ascent, descent = f.getmetrics()
    return (ascent + descent) * 1.12


def text_block_height(f: ImageFont.FreeTypeFont, line_count: int) -> float:
    if line_count <= 0:
        return 0.0
    ascent, descent = f.getmetrics()
    return line_step(f) * (line_count - 1) + ascent + descent


def mix(a: tuple, b: tuple, t: float) -> tuple:
    t = min(max(t, 0.0), 1.0)
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


@lru_cache(maxsize=64)
def emoji_font(size: int):
    """Colour emoji font for element icons, or None if unavailable."""
    for name in EMOJI_FONT_CANDIDATES:
        for directory in _FONT_DIRS:
            if directory and (Path(directory) / name).exists():
                try:
                    return ImageFont.truetype(str(Path(directory) / name), max(int(size), 6))
                except OSError:
                    continue
    return None
