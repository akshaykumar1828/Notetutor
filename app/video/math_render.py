"""
Typeset equations (LaTeX math syntax) as transparent images, locally, using
matplotlib's built-in mathtext engine (no LaTeX installation needed).
"""

import io
from functools import lru_cache

from PIL import Image


def clean_latex(text: str) -> str:
    text = (text or "").strip()
    for wrapper in ("$$", "$", "\\[", "\\]", "\\(", "\\)"):
        text = text.replace(wrapper, "")
    return text.strip()


@lru_cache(maxsize=128)
def render_formula(latex: str, pixel_size: int, color: tuple = (255, 255, 255)) -> Image.Image | None:
    """Returns an RGBA image of the formula, or None if it cannot be parsed."""
    from matplotlib.figure import Figure

    latex = clean_latex(latex)
    if not latex:
        return None
    dpi = 100
    fig = Figure(figsize=(0.01, 0.01), dpi=dpi)
    fig.text(0, 0, f"${latex}$", fontsize=pixel_size * 72 / dpi,
             color=tuple(c / 255 for c in color))
    buffer = io.BytesIO()
    try:
        fig.savefig(buffer, format="png", dpi=dpi, transparent=True,
                    bbox_inches="tight", pad_inches=0.04)
    except (ValueError, RuntimeError):
        return None
    buffer.seek(0)
    return Image.open(buffer).convert("RGBA")
