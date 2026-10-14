"""
Visual planning: chooses the simplest visual that genuinely helps
(concept map, process flow, comparison table, or key points) and fills
it with structured data taken from the scene's own narration.

Each element names the narration sentence that introduces it, so the
renderer can reveal it exactly when it is spoken about.

The model's output is validated in code; one corrective retry is made
with the specific problems listed, and if that still fails the scene
falls back to key points derived from its own takeaways.
"""

import json
import re

from app.content.narration import clean_text, content_words

from app.common.llm_client import LLMCallError, generate_json
from app.content.schemas import SceneContent
from app.video.schemas import KeyPoint, VisualPlan


class VisualPlannerError(RuntimeError):
    """Raised when visual planning fails."""


MAX_NODES = 6
MAX_STEPS = 8
MAX_COLUMNS = 4
MAX_ROWS = 6
MAX_POINTS = 6
MAX_LABEL_WORDS = 6

_METHOD_HINTS = {
    "comparative_analysis": "comparison",
    "process_flow": "process_flow",
    "step_by_step_derivation": "process_flow",
    "visual_diagram": "concept_map",
    "visualization": "concept_map",
    "recap": "key_points",
}


VISUAL_PLANNER_PROMPT = """
You are the visual designer for an educational explainer video.

The narration below will be spoken sentence by sentence while your
visual builds up on screen. Design ONE visual that helps a learner SEE
what the narration explains, using ONLY information in the scene.

Choose visual_type:

- "process_flow": the narration describes ordered stages, a procedure,
  an algorithm, a lifecycle or a workflow. Fill "steps" (2-8), in order.
  Use it ONLY when the order is part of the meaning (each stage leads
  to the next). A plain list of items, topics or prerequisites with no
  inherent order is NOT a process: use key_points or a concept_map.
  label = short name of the stage (1-6 words). detail = one short
  sentence about the stage, taken from the narration (optional).

- "comparison": the narration contrasts two to four things. Fill
  "columns" with the names of the things compared (2-4) and "rows"
  (2-6), one per criterion. Each row has "criterion" (1-4 words) and
  "cells": exactly one short entry per column, in column order, saying
  what the narration states for that thing. Never put information
  under the wrong column. If the narration says nothing for a cell,
  write "-".

- "concept_map": the narration explains components or concepts and
  how they relate. Fill "nodes" (3-6) and "edges".
  Node label = the NAME of a concept, entity or component, 1-5 words,
  a noun phrase that appears in or is directly named by the narration.
  A node is never an instruction, a sentence, or a description of a
  relationship.
  Edge = {"source": node id, "target": node id, "label": verb phrase
  of 1-3 words}. Only include a relationship the narration actually
  states or directly implies. Every node must take part in an edge.
  Keep it simple and readable, like a clean textbook figure: a tree or
  a short chain, at most 6 edges, only the most important ones. Never
  draw the same concept twice under two names (for example "X" and
  "X layer"), and do not add an arrow that skips over a node already
  connecting the two.
  Analogies, metaphors and illustrative comparisons used in the
  narration are NEVER nodes: nodes are only the real concepts being
  taught.

- "key_points": a definition, an explanation without strong structure,
  a formula, or code. Fill "points" (1-6). kind = "text" for a short
  phrase, "formula" for an equation written in LaTeX math syntax without
  $ signs (for example \\frac{a}{b}, x^{2}, \\sqrt{n}), "code" for a code
  snippet. Points must be short (max 14 words for text), not copies of
  whole narration sentences.

- "illustration": the scene is about a concrete thing whose real
  appearance or standard textbook diagram helps understanding (a physical
  device, component, organism, body part, instrument, structure, place,
  or a well-known standard diagram). Fill "image_query": 2-6 words naming
  exactly that thing as the narration names it (searched on Wikimedia
  Commons), and "points" (1-4 short points shown beside the picture).
  Never use it for abstract ideas, software concepts or comparisons.

DIAGRAMS ONLY WHERE THEY HELP: a diagram (process_flow or concept_map) is
worth showing only when the order or the relationships ARE the point of the
scene. A process_flow needs at least 3 real stages. For definitions, lists,
overviews and recaps, key_points is clearer. Prefer a comparison table for
any contrast.

Use the simplest visual that genuinely improves understanding. Do not
force a diagram onto content that has no structure; key_points is a
good choice for those scenes.

TIMING: every node, step, row and point has "sentence": the index (0-based)
of the narration sentence during which it should appear, normally the
sentence that first mentions it. Elements should appear in the order the
narration introduces them. Sentence indices must be between 0 and
{last_index}.

Element ids: short unique strings such as "n1", "s1", "r1", "p1".

ICONS: give every node, step and point an "icon": ONE emoji that
clearly represents it (for example a gear for a process, a book for
knowledge, a magnifying glass for search). Pick icons that match the
meaning; if nothing fits, use "".

Return ONLY a JSON object with this structure (fill only the fields of
the chosen visual_type, leave the others as empty lists):

{{
  "visual_type": "concept_map | process_flow | comparison | key_points | illustration",
  "image_query": "",
  "reason": "one sentence: why this visual fits",
  "nodes": [{{"id": "n1", "label": "...", "icon": "", "sentence": 0}}],
  "edges": [{{"source": "n1", "target": "n2", "label": "..."}}],
  "steps": [{{"id": "s1", "label": "...", "detail": "...", "icon": "", "sentence": 0}}],
  "columns": ["...", "..."],
  "rows": [{{"id": "r1", "criterion": "...", "cells": ["...", "..."], "sentence": 0}}],
  "points": [{{"id": "p1", "text": "...", "kind": "text", "icon": "", "sentence": 0}}]
}}
"""


_tokens = content_words


def best_sentence(text: str, sentences: list[str]) -> int:
    """Index of the sentence sharing the most words with text (-1 if none)."""
    words = _tokens(text)
    best, best_score = -1, 0.0
    for i, sentence in enumerate(sentences):
        score = len(words & _tokens(sentence)) / (len(words) or 1)
        if score > best_score:
            best, best_score = i, score
    return best


def _elements(plan: VisualPlan) -> list:
    return {
        "concept_map": plan.nodes,
        "process_flow": plan.steps,
        "comparison": plan.rows,
        "key_points": plan.points,
        "illustration": plan.points,
    }[plan.visual_type]


def _element_text(element) -> str:
    for attr in ("label", "text"):
        if hasattr(element, attr):
            return getattr(element, attr)
    return f"{element.criterion} {' '.join(element.cells)}"


def _clean_icon(icon: str) -> str:
    """Keeps a single emoji; drops text, variation selectors and junk."""
    icon = (icon or "").strip()
    if not icon or any(ch.isalnum() and ord(ch) < 0x2000 for ch in icon):
        return ""
    # Multi-part emoji (joined sequences, skin tones) render as overlapping
    # glyphs without a shaping engine, so keep only the base symbol.
    return icon[0] if ord(icon[0]) >= 0x2000 else ""


def normalize_plan(plan: VisualPlan, sentences: list[str]) -> VisualPlan:
    """Cleans ids, dangling edges and sentence cues without inventing content."""

    for n in plan.nodes:
        n.label = clean_text(n.label)
    for e in plan.edges:
        e.label = clean_text(e.label)
    for s in plan.steps:
        s.label, s.detail = clean_text(s.label), clean_text(s.detail)
    plan.columns = [clean_text(c) for c in plan.columns]
    for r in plan.rows:
        r.criterion = clean_text(r.criterion)
        r.cells = [clean_text(c) for c in r.cells]
    for p in plan.points:
        p.text = clean_text(p.text) if p.kind == "text" else p.text.strip()

    elements = _elements(plan)
    seen = set()
    for index, element in enumerate(elements):
        element.id = (element.id or "").strip() or f"e{index + 1}"
        if element.id in seen or element.id == "header":
            element.id = f"{element.id}_{index + 1}"
        seen.add(element.id)

    if plan.visual_type == "concept_map":
        ids = {n.id for n in plan.nodes}
        plan.edges = [
            e for e in plan.edges
            if e.source in ids and e.target in ids and e.source != e.target
        ]
        connected = {e.source for e in plan.edges} | {e.target for e in plan.edges}
        dropped = [n.label for n in plan.nodes if n.id not in connected]
        if dropped and plan.edges:
            print(f"[visual_planner] Dropping unconnected nodes: {dropped}")
            plan.nodes = [n for n in plan.nodes if n.id in connected]
        for n in plan.nodes:
            n.label = n.label.strip()
        elements = plan.nodes

    for element in list(plan.nodes) + list(plan.steps) + list(plan.points):
        element.icon = _clean_icon(element.icon)

    if plan.visual_type == "comparison":
        plan.columns = [c.strip() for c in plan.columns if c.strip()]
        for row in plan.rows:
            row.cells = (row.cells + ["-"] * len(plan.columns))[: len(plan.columns)]
        # A row the narration does not cover for every column is noise.
        complete = [r for r in plan.rows if all(c.strip() not in ("", "-") for c in r.cells)]
        if len(complete) >= 2:
            plan.rows = complete

    last = len(sentences) - 1
    previous = 0
    for element in elements:
        cue = element.sentence
        if not isinstance(cue, int) or cue < 0 or cue > last:
            match = best_sentence(_element_text(element), sentences)
            cue = match if match >= 0 else previous
        element.sentence = min(max(cue, 0), last)
        previous = element.sentence

    # Stages of a process must appear in order: keep the cue times the
    # model chose, but hand them out to the steps in sequence.
    # The same holds for table rows and list points: they fill in from the
    # top, so no gap is left above a row that appears early.
    if plan.visual_type in ("process_flow", "comparison", "key_points", "illustration"):
        ordered = sorted(e.sentence for e in elements)
        for element, cue in zip(elements, ordered):
            element.sentence = cue

    # A step detail that just repeats its sentence duplicates the caption.
    for step in plan.steps:
        if step.detail:
            words = content_words(step.detail)
            spoken = content_words(sentences[step.sentence])
            if words and len(words & spoken) / len(words) >= 0.8:
                step.detail = ""

    return plan


def validate_plan(plan: VisualPlan) -> list[str]:
    problems = []
    t = plan.visual_type

    if t == "concept_map":
        if not 3 <= len(plan.nodes) <= MAX_NODES:
            problems.append(f"concept_map needs 3-{MAX_NODES} connected nodes, got {len(plan.nodes)}")
        if len(plan.edges) > 7:
            problems.append(f"concept_map has {len(plan.edges)} edges; keep at most 6 key relationships")
        if len(plan.edges) < 2:
            problems.append("concept_map needs at least 2 valid edges between existing node ids")
        for n in plan.nodes:
            if len(n.label.split()) > MAX_LABEL_WORDS or n.label.endswith("."):
                problems.append(
                    f"node label '{n.label}' is a sentence or description; "
                    "node labels must be short concept names"
                )
        for e in plan.edges:
            if len(e.label.split()) > 4:
                problems.append(f"edge label '{e.label}' is too long (use 1-3 words)")
        labels = [(n.label, content_words(n.label)) for n in plan.nodes]
        for i, (a, wa) in enumerate(labels):
            for b, wb in labels[i + 1:]:
                if wa and wb and (wa <= wb or wb <= wa):
                    problems.append(
                        f"nodes '{a}' and '{b}' are the same concept; keep only one"
                    )

    elif t == "process_flow":
        if not 3 <= len(plan.steps) <= MAX_STEPS:
            problems.append(
                f"process_flow needs 3-{MAX_STEPS} real stages, got {len(plan.steps)}; "
                "for fewer, use key_points"
            )
        for s in plan.steps:
            if len(s.label.split()) > 8:
                problems.append(f"step label '{s.label}' is too long (max 6 words)")

    elif t == "comparison":
        if not 2 <= len(plan.columns) <= MAX_COLUMNS:
            problems.append(f"comparison needs 2-{MAX_COLUMNS} columns, got {len(plan.columns)}")
        if not 1 <= len(plan.rows) <= MAX_ROWS:
            problems.append(f"comparison needs 1-{MAX_ROWS} rows, got {len(plan.rows)}")
        for r in plan.rows:
            if all(c.strip() in ("", "-") for c in r.cells):
                problems.append(f"row '{r.criterion}' has no content")
        all_cells = [c.strip() for r in plan.rows for c in r.cells]
        empty = sum(c in ("", "-") for c in all_cells)
        if all_cells and empty / len(all_cells) > 0.15:
            problems.append(
                f"{empty} of {len(all_cells)} comparison cells are empty; the narration "
                "does not compare these columns on these criteria - use a different "
                "visual_type or only criteria the narration covers for every column"
            )
        cells = [c.strip().lower() for r in plan.rows for c in r.cells if c.strip() not in ("", "-")]
        if len(cells) > 3 and len(set(cells)) < len(cells) * 0.6:
            problems.append("comparison cells are mostly repeated; each cell must state what the narration says for that column")

    elif t == "illustration":
        if not plan.image_query.strip() or len(plan.image_query.split()) > 8:
            problems.append("illustration needs an image_query of 2-6 words")
        if not 1 <= len(plan.points) <= 4:
            problems.append(f"illustration needs 1-4 points, got {len(plan.points)}")

    elif t == "key_points":
        if not 1 <= len(plan.points) <= MAX_POINTS:
            problems.append(f"key_points needs 1-{MAX_POINTS} points, got {len(plan.points)}")

    return problems


def attach_image(plan: VisualPlan, context: str) -> VisualPlan:
    """Finds a verified licensed picture; without one, keeps the points only."""
    if plan.visual_type != "illustration":
        return plan
    from app.video.image_retrieval import find_teaching_image

    found = None
    try:
        found = find_teaching_image(plan.image_query, context)
    except Exception as exc:
        print(f"[visual_planner] image retrieval failed: {exc}")
    if found:
        plan.image_path, plan.image_credit = found
        return plan
    print(f"[visual_planner] No suitable image for '{plan.image_query}'; showing key points.")
    plan.visual_type = "key_points"
    return plan


def fallback_plan(scene_content: SceneContent, sentences: list[str]) -> VisualPlan:
    """Key points built from the scene's own takeaways (never invented)."""
    takeaways = [t for t in scene_content.key_takeaways if t.strip()][:MAX_POINTS]
    if not takeaways:
        takeaways = sentences[:MAX_POINTS]
    plan = VisualPlan(
        scene_id=scene_content.scene_id,
        visual_type="key_points",
        reason="fallback: key takeaways",
        points=[KeyPoint(id=f"p{i + 1}", text=t) for i, t in enumerate(takeaways)],
    )
    return normalize_plan(plan, sentences)


def _build_prompt(
    scene, scene_content: SceneContent, sentences: list[str], extra: str,
    previous_types: list[str] | None = None,
) -> str:
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
    hint = _METHOD_HINTS.get(getattr(scene, "teaching_method", ""), "")
    variety = ""
    if previous_types:
        variety = (
            f"Earlier scenes used these visuals, in order: {', '.join(previous_types)}. "
            "Viewers get bored seeing the same kind of visual again and again: prefer "
            "a different visual_type from the last one whenever the narration "
            "genuinely supports it. A contrast between things should be a comparison.\n"
        )
    hint_text = variety + (
        f'The lesson planner marked this scene as "{scene.teaching_method}", which '
        f'usually suits "{hint}" - use it if the narration supports it.\n'
        if hint else ""
    )
    scene_info = {
        "title": getattr(scene, "title", ""),
        "purpose": getattr(scene, "purpose", ""),
        "key_concepts": getattr(scene, "key_concepts", []),
        "key_takeaways": scene_content.key_takeaways,
        "example": scene_content.example,
    }
    return (
        VISUAL_PLANNER_PROMPT.replace("{last_index}", str(len(sentences) - 1))
        .replace("{{", "{").replace("}}", "}")
        + f"\n{hint_text}\nSCENE:\n{json.dumps(scene_info, indent=2)}\n"
        + f"\nNARRATION SENTENCES:\n{numbered}\n{extra}"
    )


def create_visual_plan(
    scene, scene_content: SceneContent, sentences: list[str],
    previous_types: list[str] | None = None,
) -> VisualPlan:
    if not sentences:
        raise VisualPlannerError("Visual planning needs at least one narration sentence.")

    schema = VisualPlan.model_json_schema()
    extra = ""

    for attempt in range(2):
        try:
            data = generate_json(
                _build_prompt(scene, scene_content, sentences, extra, previous_types),
                schema=schema, temperature=0.1, max_tokens=2048, label="visual_planner",
            )
            data["scene_id"] = scene_content.scene_id
            plan = normalize_plan(VisualPlan.model_validate(data), sentences)
        except (LLMCallError, ValueError) as exc:
            problems = [f"output could not be parsed: {str(exc)[:300]}"]
        else:
            problems = validate_plan(plan)
            if not problems:
                plan.reason = f"[attempt {attempt + 1}] {plan.reason}"
                return plan

        print(f"[visual_planner] attempt {attempt + 1} problems: {problems}")
        extra = (
            "\nYOUR PREVIOUS ANSWER HAD THESE PROBLEMS - fix all of them:\n- "
            + "\n- ".join(problems)
            + "\nIf the narration does not support a well-formed diagram, use key_points.\n"
        )

    print("[visual_planner] Falling back to key points from the scene takeaways.")
    return fallback_plan(scene_content, sentences)
