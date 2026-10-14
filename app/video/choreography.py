"""
Turns a visual plan plus measured narration timing into a SceneTiming:
when each element appears, while which sentences it is highlighted,
and which caption is on screen.
"""

import re

from app.content.narration import content_words

from app.video.animator import INTRO_SECONDS, Caption, SceneTiming
from app.video.schemas import VisualPlan

APPEAR_DELAY = 0.15
MENTION_THRESHOLD = 0.6


_tokens = content_words


def _mention_text(plan: VisualPlan, element) -> str:
    if plan.visual_type == "comparison":
        return element.criterion
    return getattr(element, "label", None) or getattr(element, "text", "")


def _structural_reveal(plan: VisualPlan, spans) -> dict[str, float]:
    """
    A concept map is built top-down (a concept appears before the ones it
    leads to), using the same reveal moments the narration provides, just
    handed out in structural order. The highlight still follows the
    narration, so the viewer sees the whole structure grow in a sensible
    order while the narrator walks through it.
    """
    ids = [n.id for n in plan.nodes]
    sentence_of = {n.id: n.sentence for n in plan.nodes}
    incoming = {i: 0 for i in ids}
    children: dict[str, list[str]] = {i: [] for i in ids}
    for e in plan.edges:
        if e.source in incoming and e.target in incoming:
            incoming[e.target] += 1
            children[e.source].append(e.target)

    order = []
    ready = sorted((i for i in ids if incoming[i] == 0), key=lambda i: sentence_of[i])
    remaining = dict(incoming)
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in children[node]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
        ready.sort(key=lambda i: sentence_of[i])
    # Nodes in cycles: append in narration order.
    order += sorted((i for i in ids if i not in order), key=lambda i: sentence_of[i])

    times = sorted(
        spans[min(max(sentence_of[i], 0), len(spans) - 1)][0] + APPEAR_DELAY for i in ids
    )
    return dict(zip(order, times))


def build_timing(
    plan: VisualPlan,
    sentences: list[str],
    spans: list[tuple[float, float]],
    duration: float,
) -> SceneTiming:
    if len(sentences) != len(spans):
        raise ValueError("sentences and spans must have the same length")

    elements = {
        "concept_map": plan.nodes,
        "process_flow": plan.steps,
        "comparison": plan.rows,
        "key_points": plan.points,
        "illustration": plan.points,
    }[plan.visual_type]

    cues: dict[str, float] = {}
    focus: dict[str, list[tuple[float, float]]] = {}

    if plan.visual_type == "comparison":
        cues["header"] = INTRO_SECONDS
    if plan.visual_type == "illustration":
        cues["image"] = INTRO_SECONDS

    sentence_tokens = [_tokens(s) for s in sentences]
    reveal_at = _structural_reveal(plan, spans) if plan.visual_type == "concept_map" else {}
    for element in elements:
        index = min(max(element.sentence, 0), len(spans) - 1)
        start, end = spans[index]
        cues[element.id] = reveal_at.get(element.id, start + APPEAR_DELAY)
        intervals = [(start, end)]

        # Highlight again whenever a later sentence clearly talks about it.
        words = _tokens(_mention_text(plan, element))
        if words:
            for later in range(index + 1, len(spans)):
                overlap = len(words & sentence_tokens[later]) / len(words)
                if overlap >= MENTION_THRESHOLD:
                    intervals.append(spans[later])
        focus[element.id] = intervals

    captions = [Caption(s, e, text) for text, (s, e) in zip(sentences, spans)]
    return SceneTiming(duration=duration, cues=cues, focus=focus, captions=captions)
