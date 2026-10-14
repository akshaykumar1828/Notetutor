"""
Time-driven frame renderer.

Given a Layout plus timing (when each element is introduced and while
which sentences it is being discussed), draws the frame for any time t:
elements fade/slide in at their cue, connectors draw themselves, the
element currently being talked about is highlighted, and the current
narration sentence is shown as a caption.

Sprites are rendered at 2x and downsampled for smooth edges, and are
cached by visual state so static stretches of a scene cost almost
nothing to render.
"""

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from app.video import theme
from app.video.layout import Item, Layout

SS = 2  # supersampling factor for sprites

REVEAL_SECONDS = 0.5
DRAW_SECONDS = 0.7
FOCUS_RAMP = 0.3
SLIDE_PIXELS = 12
# Boxes "pop" in: they grow from this scale with a slight overshoot.
POP_FROM = 0.82
POP_STYLES = {"node", "hub", "step", "code", "formula", "image", "point_marker"}
# The element being explained gently pulses (seconds per cycle).
PULSE_SECONDS = 1.6
# Glowing dots travel along arrows into / out of what is being explained.
FLOW_SECONDS = 1.4
PHASE_STEPS = 24

TITLE_Y = 30
CAPTION_TOP = 618

# Scene opening: the title is shown large, then moves aside for content.
INTRO_SECONDS = 1.4
# Spotlight: elements not being talked about dim to this fraction.
SPOTLIGHT_DIM = 0.72
SPOTLIGHT_STYLES = {
    "node", "hub", "step", "step_number",
    "point", "point_marker", "code", "edge", "edge_label",
}
# Slow background drift so the frame never looks frozen.
DRIFT_X, DRIFT_Y = 60, 40


@dataclass
class Caption:
    start: float
    end: float
    text: str


@dataclass
class SceneTiming:
    duration: float
    cues: dict[str, float]  # element_id -> appear time
    focus: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    captions: list[Caption] = field(default_factory=list)


def _ease_back(x: float) -> float:
    """Ease-out with a small overshoot: a soft 'pop'."""
    x = min(max(x, 0.0), 1.0)
    c = 1.4
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def _smooth(points, iterations: int = 3):
    """Rounds the corners of a bent connector (Chaikin corner cutting),
    keeping both end points exactly where they were."""
    pts = [tuple(p) for p in points]
    if len(pts) < 3:
        return pts
    for _ in range(iterations):
        out = [pts[0]]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            out.append((0.75 * x1 + 0.25 * x2, 0.75 * y1 + 0.25 * y2))
            out.append((0.25 * x1 + 0.75 * x2, 0.25 * y1 + 0.75 * y2))
        out[1], out[-1] = pts[0], pts[-1]
        pts = out[1:] if out[0] == out[1] else out
        pts[-1] = out[-1]
    return pts


def _point_along(points, fraction: float):
    lengths = [np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    target = sum(lengths) * fraction
    for (a, b), length in zip(zip(points, points[1:]), lengths):
        if target <= length or length == lengths[-1]:
            f = target / length if length else 0
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        target -= length
    return points[-1]


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def _focus_level(intervals: list[tuple[float, float]], t: float) -> float:
    level = 0.0
    for start, end in intervals:
        if start - FOCUS_RAMP <= t <= end + FOCUS_RAMP:
            rise = _ease((t - start + FOCUS_RAMP) / FOCUS_RAMP)
            fall = _ease((end + FOCUS_RAMP - t) / FOCUS_RAMP)
            level = max(level, min(rise, fall))
    return level


def _q(x: float, steps: int = 10) -> float:
    return round(x * steps) / steps


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    w, h = theme.VIDEO_WIDTH + 2 * DRIFT_X, theme.VIDEO_HEIGHT + 2 * DRIFT_Y
    ys, xs = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xs - w * 0.5) / w) ** 2 + ((ys - h * 0.35) / h) ** 2)
    t = np.clip(1 - d * 1.6, 0, 1)[..., None]
    base = np.array(theme.BACKGROUND, dtype=np.float32)
    glow = np.array(theme.BACKGROUND_GLOW, dtype=np.float32)
    img = base + (glow - base) * t
    return Image.fromarray(img.astype(np.uint8), "RGB").convert("RGBA")


def _draw_text(draw, spec, box, color, scale=SS, valign="center"):
    f = theme.font(spec.role, spec.size * scale)
    step = theme.line_step(f)
    block = theme.text_block_height(f, len(spec.lines))
    x0, y0, x1, y1 = box
    y = y0 + (y1 - y0 - block) / 2 if valign == "center" else y0
    for line in spec.lines:
        if spec.align == "left":
            draw.text((x0, y), line, font=f, fill=color, anchor="la")
        else:
            draw.text(((x0 + x1) / 2, y), line, font=f, fill=color, anchor="ma")
        y += step


class SceneAnimator:

    def __init__(
        self,
        title: str,
        layout: Layout | None,
        timing: SceneTiming,
        progress_label: str = "",
        show_captions: bool = True,
    ):
        self.title = title
        self.layout = layout
        self.timing = timing
        self.progress_label = progress_label
        self.show_captions = show_captions
        self._sprite_cache: dict = {}
        self._last_key = None
        self._last_frame = None
        self._base_key = None
        self._base = None
        self._appear = self._appear_times()
        self._title_sprite = self._render_title()
        self._intro_sprite = self._render_intro()
        # The big title stays until the first element is discussed, so the
        # screen is never empty and never shows something not yet spoken of.
        first = min(
            (t for eid, t in self._appear.items() if not eid.startswith("edge:")),
            default=INTRO_SECONDS,
        )
        self._intro_end = max(INTRO_SECONDS, first - 0.1)

    # -----------------------------------------------------------------
    # timing
    # -----------------------------------------------------------------

    def _appear_times(self) -> dict[str, float]:
        cues = dict(self.timing.cues)
        if self.layout:
            for eid, deps in self.layout.depends.items():
                known = [cues[d] for d in deps if d in cues]
                if known:
                    cues[eid] = max(known) + 0.25
        return cues

    def _item_focus(self, item: Item, t: float) -> float:
        focus = self.timing.focus
        if self.layout and item.element_id in self.layout.depends:
            deps = self.layout.depends[item.element_id]
            return sum(_focus_level(focus.get(d, []), t) for d in deps) / len(deps)
        return _focus_level(focus.get(item.element_id, []), t)

    # -----------------------------------------------------------------
    # sprites (rendered at 2x, downsampled, cached)
    # -----------------------------------------------------------------

    def _render_intro(self) -> Image.Image:
        """Large centred title shown at the start of the scene."""
        from app.video.layout import wrap_text

        img = Image.new("RGBA", (theme.VIDEO_WIDTH, theme.VIDEO_HEIGHT), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        size = 54
        lines = wrap_text(self.title, "semibold", size, 1050)
        while len(lines) > 2 and size > 34:
            size -= 4
            lines = wrap_text(self.title, "semibold", size, 1050)
        f = theme.font("semibold", size)
        step = theme.line_step(f)
        y = theme.VIDEO_HEIGHT / 2 - step * len(lines) / 2
        if self.progress_label:
            d.text((theme.VIDEO_WIDTH / 2, y - 44), self.progress_label.replace(" / ", " of "),
                   font=theme.font("regular", 22), fill=theme.ACCENT, anchor="ma")
        for line in lines:
            d.text((theme.VIDEO_WIDTH / 2, y), line, font=f, fill=theme.TEXT, anchor="ma")
            y += step
        d.line((theme.VIDEO_WIDTH / 2 - 60, y + 16, theme.VIDEO_WIDTH / 2 + 60, y + 16),
               fill=theme.ACCENT, width=4)
        return img

    def _render_title(self) -> Image.Image:
        f = theme.font("semibold", 34)
        lines = _wrap_single(self.title, f, 1000)
        img = Image.new("RGBA", (theme.VIDEO_WIDTH, 100), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.text((60, TITLE_Y), lines, font=f, fill=theme.TEXT, anchor="la")
        if self.progress_label:
            small = theme.font("regular", 18)
            d.text(
                (theme.VIDEO_WIDTH - 60, TITLE_Y + 10), self.progress_label,
                font=small, fill=theme.TEXT_MUTED, anchor="ra",
            )
        return img

    def _sprite(self, index: int, item: Item, focus: float, progress: float, pulse: float = 0.0):
        key = (index, _q(focus), _q(progress, 20), _q(pulse, 6))
        cached = self._sprite_cache.get(key)
        if cached is not None:
            return cached

        if item.kind == "connector":
            result = self._connector_sprite(item, focus, progress)
        else:
            result = self._box_sprite(item, focus, pulse)
        self._sprite_cache[key] = result
        return result

    def _box_sprite(self, item: Item, focus: float, pulse: float = 0.0):
        pad = 8
        x0, y0, x1, y1 = item.rect
        w, h = int(x1 - x0) + 2 * pad, int(y1 - y0) + 2 * pad
        img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        box = (pad * SS, pad * SS, (w - pad) * SS, (h - pad) * SS)
        style = item.style
        text_color = theme.TEXT

        if style == "image":
            from PIL import Image as PILImage
            with PILImage.open(item.image_path) as source:
                picture = source.convert("RGBA").resize(
                    (box[2] - box[0], box[3] - box[1]), PILImage.LANCZOS)
            mask = Image.new("L", picture.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, *picture.size), radius=12 * SS, fill=255)
            picture.putalpha(mask)
            img.paste(picture, (box[0], box[1]), picture)
            d.rounded_rectangle(box, radius=12 * SS, outline=theme.BORDER, width=2 * SS)
        elif style == "credit":
            text_color = (120, 132, 156)
        elif style in ("node", "hub", "step", "detail"):
            tint = item.color or (theme.ACCENT if style == "hub" else theme.BORDER)
            fill = theme.mix(theme.mix(theme.SURFACE, tint, 0.16), theme.SURFACE_FOCUS, focus * 0.5)
            border = theme.mix(theme.mix(theme.BORDER, tint, 0.75), theme.FOCUS, focus)
            if focus > 0.05:
                glow = (*theme.FOCUS, int((55 + 60 * pulse) * focus))
                d.rounded_rectangle(
                    (box[0] - 5 * SS, box[1] - 5 * SS, box[2] + 5 * SS, box[3] + 5 * SS),
                    radius=18 * SS, outline=glow, width=4 * SS,
                )
            d.rounded_rectangle(box, radius=14 * SS, fill=fill, outline=border,
                                width=int((2 + focus) * SS))
            if item.color and style != "detail":
                # colour bar along the top edge
                d.rounded_rectangle((box[0] + 10 * SS, box[1] + 3 * SS, box[2] - 10 * SS, box[1] + 7 * SS),
                                    radius=2 * SS, fill=item.color)
        elif style == "edge_label":
            d.rounded_rectangle(box, radius=10 * SS, fill=(*theme.BACKGROUND, 235))
            text_color = theme.mix(theme.TEXT_MUTED, theme.FOCUS, focus)
        elif style == "formula":
            from app.video.math_render import render_formula
            d.rounded_rectangle(box, radius=10 * SS, fill=theme.CODE_SURFACE,
                                outline=theme.mix(theme.BORDER, theme.FOCUS, focus), width=2 * SS)
            picture = render_formula(item.latex, item.text.size * SS,
                                     theme.mix((235, 240, 255), theme.FOCUS, focus * 0.6))
            if picture is not None:
                top = box[1] + (box[3] - box[1] - picture.height) // 2
                img.alpha_composite(picture, (box[0] + 18 * SS, max(top, box[1])))
        elif style == "code":
            d.rounded_rectangle(box, radius=10 * SS, fill=theme.CODE_SURFACE,
                                outline=theme.mix(theme.BORDER, theme.FOCUS, focus), width=2 * SS)
            text_color = theme.mix((210, 225, 255), theme.FOCUS, focus * 0.5)
        elif style == "table":
            d.rounded_rectangle(box, radius=14 * SS, fill=(*theme.SURFACE, 200),
                                outline=theme.BORDER, width=2 * SS)
        elif style == "row":
            # Each row brings its own background, so the table grows as
            # rows are revealed instead of starting as an empty frame.
            d.rectangle(box, fill=(*theme.SURFACE, 200))
            d.line((box[0], box[1], box[0], box[3]), fill=theme.BORDER, width=2 * SS)
            d.line((box[2], box[1], box[2], box[3]), fill=theme.BORDER, width=2 * SS)
            d.line((box[0], box[3], box[2], box[3]), fill=theme.BORDER, width=2 * SS)
            d.line((box[0] + 12 * SS, box[1], box[2] - 12 * SS, box[1]),
                   fill=(*theme.BORDER, 160), width=SS)
            if focus > 0.02:
                d.rectangle((box[0] + 2 * SS, box[1] + SS, box[2] - 2 * SS, box[3]),
                            fill=(*theme.FOCUS, int(34 * focus)))
                d.rectangle((box[0] + 2 * SS, box[1] + SS, box[0] + 7 * SS, box[3]),
                            fill=(*theme.FOCUS, int(255 * focus)))
        elif style == "point_marker":
            cx = box[0] + 10 * SS
            cy = (box[1] + box[3]) / 2
            icon_size = int(min(box[3] - box[1], box[2] - box[0] - 10 * SS) * 0.9)
            if not (item.icon and _draw_icon(d, item.icon, (box[0], box[1]),
                                             icon_size)):
                r = (7 + 2 * focus) * SS
                d.ellipse((cx - r, cy - r, cx + r, cy + r),
                          fill=theme.mix(theme.ACCENT, theme.FOCUS, focus))
        elif style == "column_header":
            text_color = item.color or theme.ACCENT
            d.rounded_rectangle((box[0] + 30 * SS, box[3] - 6 * SS, box[2] - 30 * SS, box[3] - 3 * SS),
                                radius=2 * SS, fill=text_color)
        elif style == "criterion":
            text_color = theme.mix(theme.ACCENT, theme.FOCUS, focus)
        elif style == "step_number":
            text_color = theme.mix(theme.ACCENT, theme.FOCUS, focus)
        elif style == "point":
            text_color = theme.mix((225, 232, 245), theme.TEXT, focus)

        if item.text:
            inner = box
            valign = "center"
            if style in ("code",):
                inner = (box[0] + 16 * SS, box[1] + 14 * SS, box[2] - 16 * SS, box[3] - 14 * SS)
                valign = "top"
            elif style == "point":
                valign = "top"
            elif style == "step":
                # leave room for the step number at the top
                f = theme.font("semibold", max(item.text.size - 8, 12) * SS)
                inner = (box[0], box[1] + theme.line_step(f) * 0.9, box[2], box[3])
            elif style == "detail":
                inner = (box[0] + 20 * SS, box[1], box[2] - 20 * SS, box[3])
            if item.icon and style in ("node", "hub", "step"):
                size = int(item.text.size * 1.3 * SS)
                cy = (inner[1] + inner[3]) / 2
                if _draw_icon(d, item.icon, (inner[0] + 12 * SS, cy - size / 2), size):
                    inner = (inner[0] + size + 12 * SS, inner[1], inner[2], inner[3])
            if style != "formula":
                _draw_text(d, item.text, inner, text_color, valign=valign)

        img = img.resize((w, h), Image.LANCZOS)
        return img, (int(x0) - pad, int(y0) - pad)

    def _connector_sprite(self, item: Item, focus: float, progress: float):
        xs = [p[0] for p in item.points]
        ys = [p[1] for p in item.points]
        pad = 16
        ox, oy = int(min(xs)) - pad, int(min(ys)) - pad
        w = int(max(xs) - min(xs)) + 2 * pad
        h = int(max(ys) - min(ys)) + 2 * pad
        img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = theme.mix(theme.ACCENT, theme.FOCUS, focus)
        width = int((3 + focus) * SS)

        pts = [((x - ox) * SS, (y - oy) * SS) for x, y in _smooth(item.points)]
        seg_lengths = [np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
        total = sum(seg_lengths) or 1.0
        head = 13 * SS
        # Stop the line short of the arrow tip so the head stays sharp.
        drawable = max(total * progress - (head * 0.8 if progress >= 1 and item.arrow else 0), 0)

        remaining = drawable
        drawn = [pts[0]]
        for (a, b), length in zip(zip(pts, pts[1:]), seg_lengths):
            if remaining <= 0:
                break
            if length <= remaining:
                drawn.append(b)
                remaining -= length
            else:
                f = remaining / length
                drawn.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                remaining = 0
        if len(drawn) >= 2:
            d.line(drawn, fill=color, width=width, joint="curve")

        if item.arrow and progress >= 1:
            (ax, ay), (bx, by) = pts[-2], pts[-1]
            length = np.hypot(bx - ax, by - ay) or 1.0
            ux, uy = (bx - ax) / length, (by - ay) / length
            base = (bx - ux * head, by - uy * head)
            left = (base[0] - uy * head * 0.55, base[1] + ux * head * 0.55)
            right = (base[0] + uy * head * 0.55, base[1] - ux * head * 0.55)
            d.polygon([(bx, by), left, right], fill=color)

        img = img.resize((w, h), Image.LANCZOS)
        return img, (ox, oy)

    # -----------------------------------------------------------------
    # captions
    # -----------------------------------------------------------------

    @lru_cache(maxsize=64)
    def _caption_sprite(self, text: str) -> Image.Image:
        f = theme.font("regular", 22)
        lines = []
        for size in (22, 20, 18):
            f = theme.font("regular", size)
            from app.video.layout import wrap_text
            lines = wrap_text(text, "regular", size, 1080)
            if len(lines) <= 3:
                break
        lines = lines[:3]
        step = theme.line_step(f)
        h = int(theme.text_block_height(f, len(lines)) + 20)
        img = Image.new("RGBA", (theme.VIDEO_WIDTH, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        y = 10
        for line in lines:
            d.text((theme.VIDEO_WIDTH / 2, y), line, font=f, fill=(*theme.TEXT_MUTED, 255), anchor="ma")
            y += step
        return img

    # -----------------------------------------------------------------
    # frame
    # -----------------------------------------------------------------

    def frame(self, t: float) -> np.ndarray:
        timing = self.timing
        states = []
        if self.layout:
            for index, item in enumerate(self.layout.items):
                appear = self._appear.get(item.element_id)
                if appear is None:
                    continue
                if item.kind == "connector":
                    reveal = 1.0 if t >= appear else 0.0
                    progress = _ease((t - appear) / DRAW_SECONDS)
                else:
                    reveal = _ease((t - appear) / REVEAL_SECONDS)
                    progress = 1.0
                if reveal <= 0:
                    continue
                focus = self._item_focus(item, t)
                if item.style == "detail":
                    reveal *= focus
                    if reveal <= 0.01:
                        continue
                states.append((index, _q(reveal, 20), _q(focus), _q(progress, 20)))

        # Spotlight: while something is being discussed, the rest dims.
        spot = max(
            (f for i, _, f, _ in states if self.layout.items[i].style in SPOTLIGHT_STYLES),
            default=0.0,
        )
        if spot > 0:
            dimmed = []
            for index, reveal, focus, progress in states:
                if self.layout.items[index].style in SPOTLIGHT_STYLES:
                    reveal = _q(reveal * (1 - (1 - SPOTLIGHT_DIM) * spot * (1 - focus)), 20)
                dimmed.append((index, reveal, focus, progress))
            states = dimmed

        caption = None
        caption_alpha = 0.0
        if self.show_captions:
            for c in timing.captions:
                if c.start - 0.15 <= t <= c.end + 0.15:
                    caption = c.text
                    caption_alpha = min(
                        _ease((t - c.start + 0.15) / 0.2), _ease((c.end + 0.15 - t) / 0.2)
                    )
                    break

        end = self._intro_end
        intro = _q(min(_ease(t / 0.35), 1 - _ease((t - end + 0.4) / 0.4)), 20)
        header = _q(_ease((t - end + 0.3) / 0.4), 20)
        underline = _q(_ease((t - end + 0.2) / 0.8), 20)
        fade_out = _q(_ease((timing.duration - t) / 0.45), 20)
        phase = 2 * np.pi * t
        drift = (int(DRIFT_X * (1 + np.sin(phase / 23))) // 3 * 3,
                 int(DRIFT_Y * (1 + np.sin(phase / 31 + 1))) // 3 * 3)
        # Motion that only runs while something is being explained, so
        # quiet stretches still reuse the cached frame.
        pulse_phase = flow_phase = 0
        if any(f > 0.5 for _, _, f, _ in states):
            pulse_phase = int((t % PULSE_SECONDS) / PULSE_SECONDS * PHASE_STEPS)
            flow_phase = int((t % FLOW_SECONDS) / FLOW_SECONDS * PHASE_STEPS)
        key = (tuple(states), caption, _q(caption_alpha, 10), intro, header,
               underline, fade_out, drift, pulse_phase, flow_phase)
        if key == self._last_key:
            return self._last_frame

        # Elements that look the same from frame to frame are drawn once
        # into a cached base layer; only moving ones are drawn per frame.
        def moving(state) -> bool:
            index, reveal, focus, progress = state
            item = self.layout.items[index]
            if reveal < 1 or progress < 1:
                return True
            if focus > 0.5 and item.style in ("node", "hub", "step"):
                return True  # pulsing
            return item.kind == "connector" and focus > 0.5  # flow dots

        static = [s for s in states if not moving(s)]
        dynamic = [s for s in states if moving(s)]
        base_key = (tuple(static), intro, header, underline, drift)
        if base_key != self._base_key:
            bg = _background()
            base = bg.crop((drift[0], drift[1], drift[0] + theme.VIDEO_WIDTH,
                            drift[1] + theme.VIDEO_HEIGHT))
            if intro > 0:
                _paste(base, _with_alpha(self._intro_sprite, intro), 0, 0)
            if header > 0:
                _paste(base, _with_alpha(self._title_sprite, header), 0, int(10 * (1 - header)))
                ud = ImageDraw.Draw(base)
                ud.line((60, 86, 60 + 140 * underline, 86), fill=theme.ACCENT, width=3)
            for index, reveal, focus, progress in static:
                sprite, (x, y) = self._sprite(index, self.layout.items[index], focus, progress)
                _paste(base, sprite, x, y)
            self._base_key, self._base = base_key, base
        frame = self._base.copy()

        pulse = 0.5 - 0.5 * np.cos(2 * np.pi * pulse_phase / PHASE_STEPS)
        flows = []
        for index, reveal, focus, progress in dynamic:
            item = self.layout.items[index]
            sprite, (x, y) = self._sprite(index, item, focus, progress,
                                          pulse if focus > 0.5 else 0.0)
            if reveal < 1 and item.kind != "connector":
                if item.style in POP_STYLES:
                    scale = POP_FROM + (1 - POP_FROM) * _ease_back(reveal)
                    w, h = sprite.size
                    nw, nh = max(int(w * scale), 1), max(int(h * scale), 1)
                    sprite = sprite.resize((nw, nh), Image.BILINEAR)
                    x, y = x + (w - nw) // 2, y + (h - nh) // 2
                else:
                    y += int(SLIDE_PIXELS * (1 - reveal))
            if reveal < 1:
                sprite = sprite.copy()
                alpha = sprite.getchannel("A").point(lambda v, r=reveal: int(v * r))
                sprite.putalpha(alpha)
            _paste(frame, sprite, x, y)
            if item.kind == "connector" and progress >= 1 and focus > 0.5:
                flows.append(_smooth(item.points))

        if flows and flow_phase is not None:
            fd = ImageDraw.Draw(frame)
            for points in flows:
                for k in range(2):
                    fx, fy = _point_along(points, ((flow_phase / PHASE_STEPS) + k / 2) % 1.0)
                    fd.ellipse((fx - 7, fy - 7, fx + 7, fy + 7), fill=(*theme.FOCUS, 70))
                    fd.ellipse((fx - 3.5, fy - 3.5, fx + 3.5, fy + 3.5), fill=(*theme.FOCUS, 255))

        if caption:
            sprite = self._caption_sprite(caption)
            if caption_alpha < 1:
                sprite = sprite.copy()
                sprite.putalpha(sprite.getchannel("A").point(lambda v, r=caption_alpha: int(v * r)))
            _paste(frame, sprite, 0, CAPTION_TOP)

        rgb = frame.convert("RGB")
        if fade_out < 1:
            rgb = Image.blend(Image.new("RGB", rgb.size, theme.BACKGROUND), rgb, fade_out)

        self._last_key = key
        self._last_frame = np.asarray(rgb)
        return self._last_frame


def _with_alpha(sprite: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1:
        return sprite
    faded = sprite.copy()
    faded.putalpha(faded.getchannel("A").point(lambda v, a=alpha: int(v * a)))
    return faded


def _paste(frame: Image.Image, sprite: Image.Image, x: int, y: int) -> None:
    """alpha_composite that tolerates sprites partly outside the frame."""
    fx0, fy0 = max(x, 0), max(y, 0)
    fx1 = min(x + sprite.width, frame.width)
    fy1 = min(y + sprite.height, frame.height)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    crop = sprite.crop((fx0 - x, fy0 - y, fx1 - x, fy1 - y))
    frame.alpha_composite(crop, (fx0, fy0))


def _draw_icon(draw, icon: str, xy: tuple, size: int) -> bool:
    """Draws a colour emoji icon; returns False if no emoji font exists."""
    f = theme.emoji_font(size)
    if f is None or not icon:
        return False
    # Centre the glyph vertically on the requested box.
    x, y = xy
    draw.text((x, y + size / 2), icon, font=f, embedded_color=True, anchor="lm")
    return True


def _wrap_single(text: str, f, max_width: float) -> str:
    """Titles stay on one line; shorten with an ellipsis if needed."""
    if f.getlength(text) <= max_width:
        return text
    while text and f.getlength(text + "…") > max_width:
        text = text[:-1]
    return text.rstrip() + "…"
