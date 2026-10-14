"""Geometry tests for the deterministic layout engine (no LLM needed)."""

import math

import pytest

from app.video.layout import layout_visual, rects_overlap
from app.video.schemas import (
    ComparisonRow,
    ConceptEdge,
    ConceptNode,
    KeyPoint,
    ProcessStep,
    VisualPlan,
)

REGION = (60, 110, 1220, 600)


def _graph(nodes, edges):
    return VisualPlan(
        visual_type="concept_map",
        nodes=[ConceptNode(id=i, label=l) for i, l in nodes],
        edges=[ConceptEdge(source=s, target=t, label=l) for s, t, l in edges],
    )


PLANS = {
    "networking_layers": _graph(
        [("app", "Application layer"), ("tr", "Transport layer"),
         ("net", "Network layer"), ("link", "Data link layer"),
         ("phy", "Physical layer"), ("tcp", "TCP"), ("ip", "IP")],
        [("app", "tr", "passes data to"), ("tr", "net", "segments to"),
         ("net", "link", "packets to"), ("link", "phy", "frames to"),
         ("tcp", "tr", "implements"), ("ip", "net", "implements"),
         ("app", "phy", "ultimately sent over")],
    ),
    "ml_training_cycle": _graph(
        [("d", "Training data"), ("m", "Model"), ("p", "Predictions"),
         ("l", "Loss function"), ("o", "Optimizer")],
        [("d", "m", "fed into"), ("m", "p", "produces"), ("p", "l", "compared by"),
         ("l", "o", "gradients to"), ("o", "m", "updates weights of")],
    ),
    "circuit_star": _graph(
        [("c", "Series RLC circuit"), ("r", "Resistor"), ("l", "Inductor"),
         ("k", "Capacitor"), ("v", "AC voltage source"), ("i", "Current")],
        [("c", "r", "contains"), ("c", "l", "contains"), ("c", "k", "contains"),
         ("v", "c", "drives"), ("c", "i", "carries")],
    ),
    "dense_agent_map": _graph(
        [("a", "AI agent"), ("l", "LLM core"), ("u", "User prompt"), ("t", "Toolset"),
         ("p", "Planner"), ("x", "Action"), ("m", "Memory"), ("o", "Observation"), ("g", "Goal")],
        [("a", "l", "combines"), ("a", "t", "combines"), ("a", "p", "combines"),
         ("a", "m", "combines"), ("l", "u", "interprets"), ("p", "x", "decides sequence"),
         ("l", "x", "generates"), ("t", "x", "executes"), ("x", "o", "produces"),
         ("o", "m", "stored in"), ("m", "p", "informs"), ("g", "p", "guides")],
    ),
    "scheduling_flow": VisualPlan(
        visual_type="process_flow",
        steps=[ProcessStep(id=f"s{i}", label=l, detail=f"Detail about {l.lower()}.")
               for i, l in enumerate([
                   "Process created", "Admitted to ready queue",
                   "Scheduler selects process", "Dispatched to CPU",
                   "Runs until interrupt or I/O request",
                   "Moved to waiting queue", "Terminates and releases resources"])],
    ),
    "short_flow": VisualPlan(
        visual_type="process_flow",
        steps=[ProcessStep(id=f"s{i}", label=l) for i, l in enumerate(
            ["Parse query", "Optimize plan", "Execute"])],
    ),
    "database_comparison": VisualPlan(
        visual_type="comparison",
        columns=["SQL databases", "NoSQL databases", "NewSQL databases"],
        rows=[
            ComparisonRow(id="r1", criterion="Data model",
                          cells=["Tables with fixed schema", "Documents, key-value, graphs", "Relational tables"]),
            ComparisonRow(id="r2", criterion="Scaling",
                          cells=["Mostly vertical", "Horizontal by design", "Horizontal with ACID"]),
            ComparisonRow(id="r3", criterion="Consistency",
                          cells=["Strong ACID guarantees", "Often eventual consistency", "Strong consistency"]),
            ComparisonRow(id="r4", criterion="Typical use",
                          cells=["Banking and transactional systems", "Large-scale web apps and caching", "Global transactional services"]),
        ],
    ),
    "math_points": VisualPlan(
        visual_type="key_points",
        points=[
            KeyPoint(id="p1", text="The derivative measures the instantaneous rate of change of a function."),
            KeyPoint(id="p2", text="f'(x) = lim h->0 (f(x+h) - f(x)) / h", kind="formula"),
            KeyPoint(id="p3", text="Geometrically it is the slope of the tangent line at a point."),
        ],
    ),
}


def _inside(rect, region, tol=0.5):
    return (rect[0] >= region[0] - tol and rect[1] >= region[1] - tol
            and rect[2] <= region[2] + tol and rect[3] <= region[3] + tol)


def _on_border(point, rect, tol=1.0):
    x, y = point
    within_x = rect[0] - tol <= x <= rect[2] + tol
    within_y = rect[1] - tol <= y <= rect[3] + tol
    on_v = (abs(x - rect[0]) <= tol or abs(x - rect[2]) <= tol) and within_y
    on_h = (abs(y - rect[1]) <= tol or abs(y - rect[3]) <= tol) and within_x
    return on_v or on_h


def _segment_hits_rect(p, q, rect, shrink=3.0):
    r = (rect[0] + shrink, rect[1] + shrink, rect[2] - shrink, rect[3] - shrink)
    steps = max(int(math.dist(p, q) / 2), 1)
    for k in range(steps + 1):
        x = p[0] + (q[0] - p[0]) * k / steps
        y = p[1] + (q[1] - p[1]) * k / steps
        if r[0] < x < r[2] and r[1] < y < r[3]:
            return True
    return False


@pytest.mark.parametrize("name", list(PLANS))
def test_layout_is_clean(name):
    plan = PLANS[name]
    layout = layout_visual(plan, REGION)

    boxes = [i for i in layout.items if i.kind == "box" and i.style in ("node", "hub", "step")]
    labels = [i for i in layout.items if i.style == "edge_label"]
    connectors = [i for i in layout.items if i.kind == "connector"]

    for item in layout.items:
        if item.rect:
            assert _inside(item.rect, REGION), (item.style, item.rect)
        for pt in item.points:
            assert REGION[0] - 1 <= pt[0] <= REGION[2] + 1 and REGION[1] - 1 <= pt[1] <= REGION[3] + 1

    solid = boxes + labels
    for i, a in enumerate(solid):
        for b in solid[i + 1:]:
            assert not rects_overlap(a.rect, b.rect), (a.element_id, b.element_id)

    rect_of = {b.element_id: b.rect for b in boxes}
    for c in connectors:
        src, tgt = layout.depends[c.element_id]
        first, last = c.points[0], c.points[-1]
        ends = {src: None, tgt: None}
        # Arrow ends on one endpoint's border and starts on the other's.
        assert _on_border(last, rect_of[tgt]) or _on_border(last, rect_of[src])
        assert _on_border(first, rect_of[src]) or _on_border(first, rect_of[tgt])
        for p, q in zip(c.points, c.points[1:]):
            for other_id, rect in rect_of.items():
                if other_id in ends:
                    continue
                assert not _segment_hits_rect(p, q, rect), (c.element_id, other_id)

    # Every text line must fit inside its own rectangle horizontally.
    from app.video import theme
    for item in layout.items:
        if item.text and item.rect:
            f = theme.font(item.text.role, item.text.size)
            for line in item.text.lines:
                assert f.getlength(line) <= (item.rect[2] - item.rect[0]) + 1, (item.style, line)


def test_layout_text_comes_only_from_plan():
    plan = PLANS["networking_layers"]
    layout = layout_visual(plan, REGION)
    rendered = " ".join(" ".join(i.text.lines) for i in layout.items if i.text)
    source = " ".join([n.label for n in plan.nodes] + [e.label for e in plan.edges])
    for word in rendered.replace("…", "").replace("-", " ").split():
        assert word.lower() in source.lower().replace("-", " ")


def test_formulas_are_typeset_and_bad_latex_falls_back():
    plan = VisualPlan(visual_type="key_points", points=[
        KeyPoint(id="f", kind="formula", text=r"x = \frac{-b \pm \sqrt{b^{2}-4ac}}{2a}"),
        KeyPoint(id="bad", kind="formula", text=r"\frac{unbalanced"),
    ])
    layout = layout_visual(plan, REGION)
    styles = {i.element_id: i.style for i in layout.items if i.style in ("formula", "code")}
    assert styles == {"f": "formula", "bad": "code"}
    for item in layout.items:
        if item.rect:
            assert _inside(item.rect, REGION)
