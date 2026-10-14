"""
Layout-engine evaluation on randomly generated visual plans.

    python -m evaluation.eval_layout [cases_per_type]

For every visual type it generates plans of varying size and label length
(seeded, so results are reproducible) and measures:
  success       layout produced without error
  no_overlap    no two boxes/labels overlap
  in_bounds     everything inside the visual region
  connectors    every arrow starts/ends on its own boxes and crosses no other box
  text_fits     every text line fits inside its box
  labels_shown  share of edge labels that could be placed (rest omitted)
  font          mean / minimum font size chosen
"""

import math
import random
import statistics
import sys

from app.video import theme
from app.video.layout import LayoutError, layout_visual, rects_overlap
from app.video.schemas import (
    ComparisonRow, ConceptEdge, ConceptNode, KeyPoint, ProcessStep, VisualPlan,
)

REGION = (60, 112, 1220, 600)
WORDS = (
    "data model process memory network signal energy cell layer queue graph "
    "system input output state value function rule agent tool query index "
    "pressure current voltage protein market price risk theory proof matrix "
    "vector kernel thread packet router enzyme climate population algorithm "
    "distributed scheduling transformation optimization representation"
).split()


def _phrase(rng, lo, hi):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(lo, hi))).capitalize()


def gen_concept_map(rng):
    n = rng.randint(3, 7)
    nodes = [ConceptNode(id=f"n{i}", label=_phrase(rng, 1, 4)) for i in range(n)]
    edges = []
    for i in range(1, n):  # a tree guarantees connectivity
        edges.append(ConceptEdge(source=f"n{rng.randrange(i)}", target=f"n{i}", label=_phrase(rng, 1, 3)))
    for _ in range(rng.randint(0, 2)):
        a, b = rng.sample(range(n), 2)
        edges.append(ConceptEdge(source=f"n{a}", target=f"n{b}", label=_phrase(rng, 1, 3)))
    return VisualPlan(visual_type="concept_map", nodes=nodes, edges=edges)


def gen_process_flow(rng):
    n = rng.randint(2, 8)
    return VisualPlan(visual_type="process_flow", steps=[
        ProcessStep(id=f"s{i}", label=_phrase(rng, 1, 6),
                    detail=_phrase(rng, 4, 14) if rng.random() < 0.5 else "")
        for i in range(n)
    ])


def gen_comparison(rng):
    cols = rng.randint(2, 4)
    return VisualPlan(
        visual_type="comparison",
        columns=[_phrase(rng, 1, 3) for _ in range(cols)],
        rows=[ComparisonRow(id=f"r{i}", criterion=_phrase(rng, 1, 3),
                            cells=[_phrase(rng, 1, 8) for _ in range(cols)])
              for i in range(rng.randint(1, 6))],
    )


def gen_key_points(rng):
    return VisualPlan(visual_type="key_points", points=[
        KeyPoint(id=f"p{i}", text=_phrase(rng, 3, 14)) for i in range(rng.randint(1, 6))
    ])


def _on_border(p, r, tol=1.0):
    x, y = p
    in_x, in_y = r[0] - tol <= x <= r[2] + tol, r[1] - tol <= y <= r[3] + tol
    return ((abs(x - r[0]) <= tol or abs(x - r[2]) <= tol) and in_y) or (
        (abs(y - r[1]) <= tol or abs(y - r[3]) <= tol) and in_x)


def _crosses(p, q, r, shrink=3.0):
    r = (r[0] + shrink, r[1] + shrink, r[2] - shrink, r[3] - shrink)
    steps = max(int(math.dist(p, q) / 2), 1)
    return any(
        r[0] < p[0] + (q[0] - p[0]) * k / steps < r[2]
        and r[1] < p[1] + (q[1] - p[1]) * k / steps < r[3]
        for k in range(steps + 1)
    )


def check(plan):
    try:
        layout = layout_visual(plan, REGION)
    except LayoutError:
        return None
    boxes = [i for i in layout.items if i.style in ("node", "hub", "step")]
    labels = [i for i in layout.items if i.style == "edge_label"]
    solid = boxes + labels
    no_overlap = not any(
        rects_overlap(a.rect, b.rect) for i, a in enumerate(solid) for b in solid[i + 1:]
    )
    in_bounds = all(
        (not i.rect or (i.rect[0] >= REGION[0] - 1 and i.rect[1] >= REGION[1] - 1
                        and i.rect[2] <= REGION[2] + 1 and i.rect[3] <= REGION[3] + 1))
        for i in layout.items
    )
    rect_of = {b.element_id: b.rect for b in boxes}
    connectors_ok = True
    for c in (i for i in layout.items if i.kind == "connector"):
        src, tgt = layout.depends[c.element_id]
        ends = (rect_of[src], rect_of[tgt])
        if not (any(_on_border(c.points[0], r) for r in ends)
                and any(_on_border(c.points[-1], r) for r in ends)):
            connectors_ok = False
        for p, q in zip(c.points, c.points[1:]):
            if any(_crosses(p, q, r) for eid, r in rect_of.items() if eid not in (src, tgt)):
                connectors_ok = False
    text_fits = all(
        theme.font(i.text.role, i.text.size).getlength(line) <= i.rect[2] - i.rect[0] + 1
        for i in layout.items if i.text and i.rect for line in i.text.lines
    )
    labelled = sum(1 for e in plan.edges if e.label.strip())
    return {
        "no_overlap": no_overlap, "in_bounds": in_bounds, "connectors": connectors_ok,
        "text_fits": text_fits, "font": layout.font_size,
        "labels_shown": len(labels) / labelled if labelled else None,
    }


def main(cases: int = 300) -> str:
    rng = random.Random(42)
    rows = ["| Visual type | Cases | Success | No overlap | In bounds | Connectors OK | Text fits | Edge labels shown | Font mean / min |",
            "|---|---|---|---|---|---|---|---|---|"]
    for name, gen in (("concept_map", gen_concept_map), ("process_flow", gen_process_flow),
                      ("comparison", gen_comparison), ("key_points", gen_key_points)):
        results = [check(gen(rng)) for _ in range(cases)]
        ok = [r for r in results if r]
        pct = lambda key: f"{100 * sum(r[key] for r in ok) / len(ok):.1f}%"
        shown = [r["labels_shown"] for r in ok if r["labels_shown"] is not None]
        fonts = [r["font"] for r in ok if r["font"]]
        rows.append(
            f"| {name} | {cases} | {100 * len(ok) / cases:.1f}% | {pct('no_overlap')} | "
            f"{pct('in_bounds')} | {pct('connectors')} | {pct('text_fits')} | "
            f"{f'{100 * statistics.mean(shown):.1f}%' if shown else 'n/a'} | "
            f"{statistics.mean(fonts):.1f} / {min(fonts)} |"
        )
    table = "\n".join(rows)
    print(table)
    return table


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
