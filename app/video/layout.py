"""
Deterministic layout engine: turns a VisualPlan into positioned
primitives (boxes, text, connectors) inside a rectangular region.

Nothing here is subject-specific. Sizes, wrapping, positions and
connector routes are all computed from the plan's actual content,
shrinking the font until everything fits without overlapping.

Element ids on items tell the animator WHEN each item appears:
an item appears at its element's cue; connectors list the element ids
they depend on in Layout.depends and appear once all of those are shown.
"""

import math
import re
from dataclasses import dataclass, field

from app.video import theme
from app.video.schemas import VisualPlan

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


class LayoutError(RuntimeError):
    """Raised when a plan cannot be laid out at all."""


@dataclass
class TextSpec:
    lines: list[str]
    role: str
    size: int
    align: str = "center"  # "center" | "left"


@dataclass
class Item:
    element_id: str
    kind: str  # "box" | "text" | "band" | "connector"
    style: str
    rect: Rect | None = None
    points: list[Point] = field(default_factory=list)
    text: TextSpec | None = None
    arrow: bool = False
    image_path: str = ""
    icon: str = ""
    color: tuple | None = None
    latex: str = ""


@dataclass
class Layout:
    items: list[Item]
    order: list[str]  # element ids in logical reading order
    depends: dict[str, list[str]] = field(default_factory=dict)
    font_size: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------

def wrap_text(text: str, role: str, size: int, max_width: float) -> list[str]:
    """Greedy word wrap; words wider than max_width are hyphen-split."""
    f = theme.font(role, size)
    lines: list[str] = []
    current = ""

    words = []
    for word in text.split():
        # Prefer breaking at the word's own hyphens over splitting letters.
        if f.getlength(word) > max_width and "-" in word.strip("-"):
            words.extend(p for p in re.split(r"(?<=-)", word) if p)
        else:
            words.append(word)

    for word in words:
        while f.getlength(word) > max_width and len(word) > 2:
            cut = len(word) - 1
            while cut > 1 and f.getlength(word[:cut] + "-") > max_width:
                cut -= 1
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:cut] + "-")
            word = word[cut:]

        joiner = "" if current.endswith("-") and not current.endswith(" -") else " "
        trial = f"{current}{joiner}{word}".strip()
        if f.getlength(trial) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or [""]


def splits_words(text: str, role: str, size: int, max_width: float) -> bool:
    """True if some word (or hyphen part) is wider than max_width."""
    f = theme.font(role, size)
    parts = [p for w in text.split() for p in re.split(r"(?<=-)", w) if p]
    return any(f.getlength(p) > max_width for p in parts)


def icon_width(icon: str, size: int) -> float:
    return size * 1.35 + 6 if icon else 0.0


def _text_width(lines: list[str], role: str, size: int) -> float:
    f = theme.font(role, size)
    return max((f.getlength(line) for line in lines), default=0.0)


def _text_height(lines: list[str], role: str, size: int) -> float:
    return theme.text_block_height(theme.font(role, size), len(lines))


def _truncate(lines: list[str], max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    kept[-1] = kept[-1].rstrip(" -") + "…"
    return kept


def rects_overlap(a: Rect, b: Rect, margin: float = 0.0) -> bool:
    return not (
        a[2] + margin <= b[0]
        or b[2] + margin <= a[0]
        or a[3] + margin <= b[1]
        or b[3] + margin <= a[1]
    )


def _clip_to_rect(center: Point, toward: Point, rect: Rect) -> Point:
    """Point where the ray center->toward leaves rect."""
    cx, cy = center
    dx, dy = toward[0] - cx, toward[1] - cy
    half_w = (rect[2] - rect[0]) / 2
    half_h = (rect[3] - rect[1]) / 2
    scales = []
    if abs(dx) > 1e-9:
        scales.append(half_w / abs(dx))
    if abs(dy) > 1e-9:
        scales.append(half_h / abs(dy))
    s = min(scales) if scales else 0.0
    return (cx + dx * s, cy + dy * s)


def _center(rect: Rect) -> Point:
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


# ---------------------------------------------------------------------
# Edge labels: place on a segment without hitting boxes or other labels
# ---------------------------------------------------------------------

def _place_label(
    segments: list[tuple[Point, Point]],
    lines: list[str],
    size: int,
    obstacles: list[Rect],
    soft: list[Rect],
    region: Rect,
) -> tuple[Rect, bool]:
    """Hard obstacles (boxes, labels) must be avoided; soft ones (other
    connectors) are avoided when possible."""
    w = _text_width(lines, "regular", size) + 14
    h = _text_height(lines, "regular", size) + 8
    best: Rect | None = None
    best_score = None
    for (x1, y1), (x2, y2) in segments:
        rect, score = _best_on_segment(x1, y1, x2, y2, w, h, obstacles, soft, region)
        if score == 0:
            return rect, True
        if best_score is None or score < best_score:
            best, best_score = rect, score
    return best, best_score < 100


def _best_on_segment(x1, y1, x2, y2, w, h, obstacles, soft, region):
    best, best_score = None, None
    length = math.dist((x1, y1), (x2, y2)) or 1.0
    nx, ny = -(y2 - y1) / length, (x2 - x1) / length
    side = abs(nx) * w / 2 + abs(ny) * h / 2 + 6
    candidates = [
        (t, o)
        for o in (0.0, side, -side)
        for t in (0.5, 0.38, 0.62, 0.28, 0.72, 0.2, 0.8)
    ]
    for t, o in candidates:
        cx = x1 + (x2 - x1) * t + nx * o
        cy = y1 + (y2 - y1) * t + ny * o
        rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        inside = (
            rect[0] >= region[0] and rect[2] <= region[2]
            and rect[1] >= region[1] and rect[3] <= region[3]
        )
        hard = sum(rects_overlap(rect, ob, 2) for ob in obstacles) + (0 if inside else 1)
        score = hard * 100 + sum(rects_overlap(rect, sb) for sb in soft)
        if score == 0:
            return rect, 0
        if best_score is None or score < best_score:
            best, best_score = rect, score
    return best, best_score


def _connector_samples(points: list[Point], step: float = 10.0) -> list[Rect]:
    rects = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        n = max(int(math.dist((x1, y1), (x2, y2)) / step), 1)
        for k in range(n + 1):
            x = x1 + (x2 - x1) * k / n
            y = y1 + (y2 - y1) * k / n
            rects.append((x - 2, y - 2, x + 2, y + 2))
    return rects


def _labels_to_show(edges: list) -> set:
    """When several edges into (or out of) one node share the same label,
    show that label once instead of repeating it on every arrow."""
    keep = {(e.source, e.target) for e in edges if e.label.strip()}
    keyers = (
        lambda e: (e.target, e.label.strip().lower()),
        lambda e: (e.source, e.label.strip().lower()),
    )
    for key_of in keyers:
        groups: dict = {}
        for e in edges:
            if e.label.strip() and (e.source, e.target) in keep:
                groups.setdefault(key_of(e), []).append(e)
        for group in groups.values():
            if len(group) > 1:
                middle = group[len(group) // 2]
                for e in group:
                    if e is not middle:
                        keep.discard((e.source, e.target))
    return keep


def _add_edge_labels(
    layout: Layout,
    labelled: list[tuple[str, str, list[tuple[Point, Point]]]],
    box_rects: list[Rect],
    size: int,
    region: Rect,
    wrap_width: float = 170,
    drop_colliding: bool = False,
) -> bool:
    """Returns False if some label could not be placed cleanly."""
    obstacles = list(box_rects)
    connectors = {i.element_id: i.points for i in layout.items if i.kind == "connector"}
    clean = True
    for element_id, label, segments in labelled:
        soft = [
            r for eid, pts in connectors.items() if eid != element_id
            for r in _connector_samples(pts)
        ]
        widest_word = max((theme.font("regular", size).getlength(w) for w in label.split()), default=0)
        lines = _truncate(wrap_text(label, "regular", size, max(wrap_width, widest_word + 1)), 3)
        rect, ok = _place_label(segments, lines, size, obstacles, soft, region)
        if not ok and drop_colliding:
            layout.warnings.append(f"edge label '{label}' omitted (no free space)")
            continue
        clean = clean and ok
        obstacles.append(rect)
        layout.items.append(
            Item(
                element_id=element_id,
                kind="box",
                style="edge_label",
                rect=rect,
                text=TextSpec(lines, "regular", size),
            )
        )
    return clean


# ---------------------------------------------------------------------
# Concept map
# ---------------------------------------------------------------------

def _edge_id(source: str, target: str) -> str:
    return f"edge:{source}->{target}"


def _clean_graph(plan: VisualPlan) -> tuple[list, list]:
    ids = [n.id for n in plan.nodes]
    known = set(ids)
    seen = set()
    edges = []
    for e in plan.edges:
        key = (e.source, e.target)
        if e.source in known and e.target in known and e.source != e.target and key not in seen:
            seen.add(key)
            edges.append(e)
    return plan.nodes, edges


def _layer_nodes(node_ids: list[str], edges: list) -> tuple[dict, set]:
    """Longest-path layering after DFS cycle breaking."""
    adjacency = {n: [] for n in node_ids}
    for e in edges:
        adjacency[e.source].append(e.target)

    state: dict[str, int] = {}
    reversed_pairs: set = set()

    def visit(u: str) -> None:
        state[u] = 1
        for v in adjacency[u]:
            if state.get(v) == 1:
                reversed_pairs.add((u, v))
            elif v not in state:
                visit(v)
        state[u] = 2

    for n in node_ids:
        if n not in state:
            visit(n)

    preds = {n: [] for n in node_ids}
    for e in edges:
        s, t = e.source, e.target
        if (s, t) in reversed_pairs:
            s, t = t, s
        preds[t].append(s)

    layer: dict[str, int] = {}

    def depth(n: str) -> int:
        if n not in layer:
            layer[n] = 0
            layer[n] = max((depth(p) + 1 for p in preds[n]), default=0)
        return layer[n]

    for n in node_ids:
        depth(n)

    return layer, reversed_pairs


def _layered_concept_map(
    plan: VisualPlan, region: Rect, size: int, vertical: bool,
    allow_collision: bool = False, drop_labels: bool = False,
) -> Layout | None:
    nodes, edges = _clean_graph(plan)
    node_ids = [n.id for n in nodes]
    labels = {n.id: n.label for n in nodes}
    icons = {n.id: n.icon for n in nodes}
    colors = {n.id: theme.PALETTE[i % len(theme.PALETTE)] for i, n in enumerate(nodes)}
    layer, reversed_pairs = _layer_nodes(node_ids, edges)

    # Build layer membership with dummy nodes for long edges.
    layer_count = max(layer.values(), default=0) + 1
    members: list[list[str]] = [[] for _ in range(layer_count)]
    for n in node_ids:
        members[layer[n]].append(n)

    chains: list[tuple[object, list[str], bool]] = []
    dag_preds: dict[str, list[str]] = {n: [] for n in node_ids}
    for e in edges:
        s, t = e.source, e.target
        flipped = (s, t) in reversed_pairs
        if flipped:
            s, t = t, s
        chain = [s]
        for k in range(layer[s] + 1, layer[t]):
            dummy = f"__dummy_{s}_{t}_{k}"
            members[k].append(dummy)
            layer[dummy] = k
            dag_preds[dummy] = [chain[-1]]
            chain.append(dummy)
        chain.append(t)
        dag_preds.setdefault(t, []).append(chain[-2])
        chains.append((e, chain, flipped))

    dag_succs: dict[str, list[str]] = {}
    for child, parents in dag_preds.items():
        for p in parents:
            dag_succs.setdefault(p, []).append(child)

    # Barycenter ordering sweeps to reduce crossings.
    position = {n: i for row in members for i, n in enumerate(row)}
    for _ in range(6):
        for rng, nbrs in (
            (range(1, layer_count), dag_preds),
            (range(layer_count - 2, -1, -1), dag_succs),
        ):
            for k in rng:
                def bary(n: str) -> float:
                    ps = nbrs.get(n, [])
                    return sum(position[p] for p in ps) / len(ps) if ps else position[n]
                members[k].sort(key=bary)
                for i, n in enumerate(members[k]):
                    position[n] = i

    # Node sizes.
    pad_x, pad_y = size * 0.8, size * 0.5
    max_text_w = 230 if vertical else 210
    sizes: dict[str, tuple[float, float, list[str]]] = {}
    for n in node_ids:
        if size > 14 and splits_words(labels[n], "semibold", size, max_text_w):
            return None
        lines = wrap_text(labels[n], "semibold", size, max_text_w)
        if len(lines) > 3:
            return None
        w = max(_text_width(lines, "semibold", size) + 2 * pad_x
                + icon_width(icons[n], size), size * 4.5)
        h = max(_text_height(lines, "semibold", size), size * 1.35 if icons[n] else 0) + 2 * pad_y
        sizes[n] = (w, h, lines)

    label_size = max(size - 5, 13)
    label_wrap = 230 if vertical else 100
    label_dims = [
        _truncate(wrap_text(e.label.strip(), "regular", label_size, label_wrap), 3)
        for e in edges if e.label.strip()
    ]
    has_labels = bool(label_dims) and not drop_labels
    label_w = max((_text_width(l, "regular", label_size) for l in label_dims), default=0) + 14
    label_h = max((_text_height(l, "regular", label_size) for l in label_dims), default=0) + 8
    dummy_span = 26
    rx0, ry0, rx1, ry1 = region
    W, H = rx1 - rx0, ry1 - ry0
    rects: dict[str, Rect] = {}
    dummy_spans: dict[str, tuple[Point, Point]] = {}

    if vertical:
        gap_x = max(size * 1.4, 26)
        gap_y = (label_h + 30) if has_labels else 50
        row_h = [max((sizes[n][1] for n in row if n in sizes), default=0) or 0 for row in members]
        row_h = [h if h else 20 for h in row_h]
        total_h = sum(row_h) + gap_y * (layer_count - 1)
        if total_h > H:
            return None
        y = ry0 + (H - total_h) / 2
        for k, row in enumerate(members):
            widths = [sizes[n][0] if n in sizes else dummy_span for n in row]
            total_w = sum(widths) + gap_x * (len(row) - 1)
            if total_w > W:
                return None
            x = rx0 + (W - total_w) / 2
            for n, w in zip(row, widths):
                if n in sizes:
                    rects[n] = (x, y, x + w, y + row_h[k])
                else:
                    cx = x + w / 2
                    dummy_spans[n] = ((cx, y), (cx, y + row_h[k]))
                x += w + gap_x
            y += row_h[k] + gap_y
    else:
        gap_y = max(size * 0.9, 18)
        gap_x = (label_w + 22) if has_labels else 64
        col_w = [max((sizes[n][0] for n in col if n in sizes), default=0) or dummy_span for col in members]
        total_w = sum(col_w) + gap_x * (layer_count - 1)
        if total_w > W:
            return None
        x = rx0 + (W - total_w) / 2
        for k, col in enumerate(members):
            heights = [sizes[n][1] if n in sizes else dummy_span for n in col]
            total_h = sum(heights) + gap_y * (len(col) - 1)
            if total_h > H:
                return None
            y = ry0 + (H - total_h) / 2
            for n, h in zip(col, heights):
                if n in sizes:
                    rects[n] = (x, y, x + col_w[k], y + h)
                else:
                    cy = y + h / 2
                    dummy_spans[n] = ((x, cy), (x + col_w[k], cy))
                y += h + gap_y
            x += col_w[k] + gap_x

    layout = Layout(items=[], order=list(node_ids), font_size=size)

    # Ports: spread several connectors along one box side.
    out_ports: dict[str, list[str]] = {}
    in_ports: dict[str, list[str]] = {}
    for e, chain, flipped in chains:
        out_ports.setdefault(chain[0], []).append(chain[1])
        in_ports.setdefault(chain[-1], []).append(chain[-2])

    def anchor(n: str) -> Point:
        if n in rects:
            return _center(rects[n])
        a, b = dummy_spans[n]
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    def port(n: str, other: str, outgoing: bool) -> Point:
        rect = rects[n]
        group = out_ports[n] if outgoing else in_ports[n]
        ordered = sorted(group, key=lambda o: anchor(o)[0 if vertical else 1])
        i = ordered.index(other)
        frac = (i + 1) / (len(ordered) + 1)
        if vertical:
            x = rect[0] + (rect[2] - rect[0]) * frac
            return (x, rect[3] if outgoing else rect[1])
        y = rect[1] + (rect[3] - rect[1]) * frac
        return (rect[2] if outgoing else rect[0], y)

    labelled = []
    show = _labels_to_show(edges)
    for e, chain, flipped in chains:
        pts: list[Point] = [port(chain[0], chain[1], True)]
        for d in chain[1:-1]:
            a, b = dummy_spans[d]
            pts.extend([a, b])
        pts.append(port(chain[-1], chain[-2], False))
        if flipped:
            pts.reverse()
        eid = _edge_id(e.source, e.target)
        layout.depends[eid] = [e.source, e.target]
        layout.items.append(
            Item(element_id=eid, kind="connector", style="edge", points=pts, arrow=True)
        )
        if e.label.strip() and (e.source, e.target) in show:
            # Only segments crossing the empty band between layers may
            # carry a label (odd segments run beside boxes inside a layer).
            gaps = [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
            mid = len(gaps) // 2
            gaps = [g for _, g in sorted(enumerate(gaps), key=lambda ig: abs(ig[0] - mid))]
            labelled.append((eid, e.label.strip(), gaps))

    for n in node_ids:
        w, h, lines = sizes[n]
        layout.items.append(
            Item(
                element_id=n, kind="box", style="node", rect=rects[n],
                text=TextSpec(lines, "semibold", size),
                icon=icons[n], color=colors[n],
            )
        )

    if not _add_edge_labels(layout, labelled, list(rects.values()), label_size, region,
                            label_wrap, drop_colliding=drop_labels):
        layout.warnings.append("edge label collision")
        return layout if allow_collision else None

    return layout


def _star_hub(plan: VisualPlan) -> str | None:
    nodes, edges = _clean_graph(plan)
    if len(nodes) < 5:
        return None
    for n in nodes:
        incident = [e for e in edges if n.id in (e.source, e.target)]
        if len(incident) == len(edges) == len(nodes) - 1:
            return n.id
    return None


def _radial_concept_map(plan: VisualPlan, region: Rect, size: int, hub: str) -> Layout | None:
    nodes, edges = _clean_graph(plan)
    labels = {n.id: n.label for n in nodes}
    icons = {n.id: n.icon for n in nodes}
    colors = {n.id: theme.PALETTE[i % len(theme.PALETTE)] for i, n in enumerate(nodes)}
    spokes = [n.id for n in nodes if n.id != hub]
    pad_x, pad_y = size * 0.8, size * 0.5
    sizes = {}
    for n in [hub] + spokes:
        role_size = size + 2 if n == hub else size
        lines = wrap_text(labels[n], "semibold", role_size, 200)
        if len(lines) > 3:
            return None
        w = max(_text_width(lines, "semibold", role_size) + 2 * pad_x
                + icon_width(icons[n], role_size), size * 4.5)
        h = max(_text_height(lines, "semibold", role_size),
                role_size * 1.35 if icons[n] else 0) + 2 * pad_y
        sizes[n] = (w, h, lines, role_size)

    rx0, ry0, rx1, ry1 = region
    cx, cy = (rx0 + rx1) / 2, (ry0 + ry1) / 2
    max_w = max(sizes[s][0] for s in spokes)
    max_h = max(sizes[s][1] for s in spokes)
    radius_x = (rx1 - rx0) / 2 - max_w / 2 - 4
    radius_y = (ry1 - ry0) / 2 - max_h / 2 - 4
    if radius_x <= 0 or radius_y <= 0:
        return None

    rects: dict[str, Rect] = {}
    hw, hh = sizes[hub][0], sizes[hub][1]
    rects[hub] = (cx - hw / 2, cy - hh / 2, cx + hw / 2, cy + hh / 2)
    count = len(spokes)
    for i, s in enumerate(spokes):
        angle = -math.pi / 2 + 2 * math.pi * i / count
        sx = cx + radius_x * math.cos(angle)
        sy = cy + radius_y * math.sin(angle)
        w, h = sizes[s][0], sizes[s][1]
        rects[s] = (sx - w / 2, sy - h / 2, sx + w / 2, sy + h / 2)

    all_rects = list(rects.values())
    for i in range(len(all_rects)):
        for j in range(i + 1, len(all_rects)):
            if rects_overlap(all_rects[i], all_rects[j], 22):
                return None

    layout = Layout(items=[], order=[hub] + spokes, font_size=size)
    label_size = max(size - 5, 13)
    labelled = []
    show = _labels_to_show(edges)
    for e in edges:
        a, b = rects[e.source], rects[e.target]
        p1 = _clip_to_rect(_center(a), _center(b), a)
        p2 = _clip_to_rect(_center(b), _center(a), b)
        eid = _edge_id(e.source, e.target)
        layout.depends[eid] = [e.source, e.target]
        layout.items.append(
            Item(element_id=eid, kind="connector", style="edge", points=[p1, p2], arrow=True)
        )
        if e.label.strip() and (e.source, e.target) in show:
            labelled.append((eid, e.label.strip(), [(p1, p2)]))

    for n in [hub] + spokes:
        w, h, lines, role_size = sizes[n]
        layout.items.append(
            Item(
                element_id=n, kind="box", style="hub" if n == hub else "node",
                rect=rects[n], text=TextSpec(lines, "semibold", role_size),
                icon=icons[n], color=colors[n],
            )
        )

    if not _add_edge_labels(layout, labelled, all_rects, label_size, region):
        return None
    return layout


def _spread(layout: Layout) -> float:
    boxes = [i.rect for i in layout.items if i.style in ("node", "hub")]
    if not boxes:
        return 0.0
    w = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    h = max(b[3] for b in boxes) - min(b[1] for b in boxes)
    return w * h


def layout_concept_map(plan: VisualPlan, region: Rect) -> Layout:
    if not plan.nodes:
        raise LayoutError("concept_map has no nodes")
    hub = _star_hub(plan)
    for size in range(28, 17, -2):
        candidates = []
        if hub:
            candidates.append(_radial_concept_map(plan, region, size, hub))
        candidates.append(_layered_concept_map(plan, region, size, vertical=True))
        candidates.append(_layered_concept_map(plan, region, size, vertical=False))
        valid = [c for c in candidates if c is not None]
        if valid:
            # Same font size: take the arrangement that fills more of the frame.
            return max(valid, key=_spread)
    # Prefer a readable size with a few edge labels omitted over tiny text.
    for size in range(24, 13, -2):
        for vertical in (True, False):
            candidate = _layered_concept_map(plan, region, size, vertical, drop_labels=True)
            if candidate is not None:
                print(f"[layout] concept_map warnings: {candidate.warnings}")
                return candidate
    raise LayoutError(
        f"concept_map with {len(plan.nodes)} nodes does not fit the frame"
    )


# ---------------------------------------------------------------------
# Process flow
# ---------------------------------------------------------------------

def _process_flow_at(plan: VisualPlan, region: Rect, size: int, rows: int) -> Layout | None:
    steps = plan.steps
    n = len(steps)
    per_row = math.ceil(n / rows)
    rx0, ry0, rx1, ry1 = region
    W, H = rx1 - rx0, ry1 - ry0
    gap_x = max(size * 2.0, 46)
    gap_y = max(size * 2.2, 54)
    box_w = min(300, (W - gap_x * (per_row - 1)) / per_row)
    if box_w < size * 5:
        return None

    pad_x, pad_y = size * 0.7, size * 0.6
    number_size = max(size - 8, 12)
    wrapped = []
    for step in steps:
        text_w = box_w - 2 * pad_x - icon_width(step.icon, size)
        if size > 14 and splits_words(step.label, "semibold", size, text_w):
            return None
        lines = wrap_text(step.label, "semibold", size, text_w)
        if len(lines) > 4:
            return None
        wrapped.append(lines)
    number_h = _text_height(["1"], "semibold", number_size) + 6
    box_h = max(_text_height(l, "semibold", size) for l in wrapped) + 2 * pad_y + number_h

    detail_size = max(size - 4, 15)
    has_detail = any(s.detail.strip() for s in steps)
    detail_lines = []
    detail_h = 0.0
    if has_detail:
        for step in steps:
            lines = wrap_text(step.detail.strip(), "regular", detail_size, W - 80)
            detail_lines.append(_truncate(lines, 3))
        detail_h = max(_text_height(l, "regular", detail_size) for l in detail_lines) + 28

    flow_h = rows * box_h + (rows - 1) * gap_y
    total_h = flow_h + (detail_h + 24 if has_detail else 0)
    if total_h > H:
        return None

    grid_w = per_row * box_w + (per_row - 1) * gap_x
    x_start = rx0 + (W - grid_w) / 2
    y_start = ry0 + (H - total_h) / 2

    layout = Layout(items=[], order=[s.id for s in steps], font_size=size)
    rects = []
    for i, step in enumerate(steps):
        r, c = divmod(i, per_row)
        x = x_start + c * (box_w + gap_x)
        y = y_start + r * (box_h + gap_y)
        rects.append((x, y, x + box_w, y + box_h))

    for i in range(n - 1):
        a, b = rects[i], rects[i + 1]
        eid = _edge_id(steps[i].id, steps[i + 1].id)
        layout.depends[eid] = [steps[i].id, steps[i + 1].id]
        if (i // per_row) == ((i + 1) // per_row):
            ymid = (a[1] + a[3]) / 2
            pts = [(a[2], ymid), (b[0], ymid)]
        else:
            # Row wrap: route through the empty band between rows.
            mid_y = a[3] + gap_y / 2
            ax = (a[0] + a[2]) / 2
            bx = (b[0] + b[2]) / 2
            pts = [(ax, a[3]), (ax, mid_y), (bx, mid_y), (bx, b[1])]
        layout.items.append(
            Item(element_id=eid, kind="connector", style="edge", points=pts, arrow=True)
        )

    for i, step in enumerate(steps):
        layout.items.append(
            Item(
                element_id=step.id, kind="box", style="step", rect=rects[i],
                text=TextSpec(wrapped[i], "semibold", size),
                icon=step.icon, color=theme.PALETTE[i % len(theme.PALETTE)],
            )
        )
        x0, y0, x1, _ = rects[i]
        layout.items.append(
            Item(
                element_id=step.id, kind="text", style="step_number",
                rect=(x0, y0 + pad_y * 0.6, x1, y0 + pad_y * 0.6 + number_h),
                text=TextSpec([str(i + 1)], "semibold", number_size),
            )
        )

    if has_detail:
        dy = y_start + flow_h + 24
        panel = (rx0 + 20, dy, rx1 - 20, dy + detail_h)
        for step, lines in zip(steps, detail_lines):
            if step.detail.strip():
                layout.items.append(
                    Item(
                        element_id=step.id, kind="box", style="detail", rect=panel,
                        text=TextSpec(lines, "regular", detail_size),
                    )
                )

    return layout


def layout_process_flow(plan: VisualPlan, region: Rect) -> Layout:
    if not plan.steps:
        raise LayoutError("process_flow has no steps")
    max_rows = min(len(plan.steps), 3)
    for size in range(28, 13, -2):
        for rows in range(1, max_rows + 1):
            result = _process_flow_at(plan, region, size, rows)
            if result is not None:
                return result
    raise LayoutError(f"process_flow with {len(plan.steps)} steps does not fit")


# ---------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------

def _comparison_at(plan: VisualPlan, region: Rect, size: int, force: bool) -> Layout | None:
    columns = plan.columns
    rows = plan.rows
    rx0, ry0, rx1, ry1 = region
    W, H = rx1 - rx0, ry1 - ry0
    has_criteria = any(r.criterion.strip() for r in rows)
    crit_w = W * 0.22 if has_criteria else 0
    cell_w = (W - crit_w) / len(columns)
    pad_x, pad_y = 14, size * 0.45
    max_lines = 4 if not force else 3

    header_size = size + 2
    header_lines = [
        _truncate(wrap_text(c, "semibold", header_size, cell_w - 2 * pad_x), 2)
        for c in columns
    ]
    header_h = max(_text_height(l, "semibold", header_size) for l in header_lines) + 2 * pad_y

    row_data = []
    for row in rows:
        crit = wrap_text(row.criterion, "semibold", size - 2, crit_w - 2 * pad_x) if has_criteria else []
        cells = [
            wrap_text(cell, "regular", size, cell_w - 2 * pad_x)
            for cell in (row.cells + [""] * len(columns))[: len(columns)]
        ]
        if not force and max(len(c) for c in cells + [crit or [""]]) > max_lines:
            return None
        crit = _truncate(crit, max_lines)
        cells = [_truncate(c, max_lines) for c in cells]
        h = max(
            [_text_height(c, "regular", size) for c in cells]
            + [_text_height(crit, "semibold", size - 2) if crit else 0]
        ) + 2 * pad_y
        row_data.append((row, crit, cells, h))

    total_h = header_h + sum(r[3] for r in row_data)
    if total_h > H and not force:
        return None

    layout = Layout(items=[], order=["header"] + [r.id for r in rows], font_size=size)
    y = ry0 + max((H - total_h) / 2, 0)
    header_band = (rx0, y, rx1, y + header_h)
    layout.items.append(Item(element_id="header", kind="band", style="table", rect=header_band))

    for i, lines in enumerate(header_lines):
        x = rx0 + crit_w + i * cell_w
        layout.items.append(
            Item(
                element_id="header", kind="text", style="column_header",
                rect=(x, y, x + cell_w, y + header_h),
                text=TextSpec(lines, "semibold", header_size),
                color=theme.PALETTE[i % len(theme.PALETTE)],
            )
        )
    y += header_h

    for row, crit, cells, h in row_data:
        if y + h > ry1 + 0.5:
            layout.warnings.append(f"row '{row.criterion}' dropped: no space")
            continue
        layout.items.append(
            Item(element_id=row.id, kind="band", style="row", rect=(rx0, y, rx1, y + h))
        )
        if crit:
            layout.items.append(
                Item(
                    element_id=row.id, kind="text", style="criterion",
                    rect=(rx0 + pad_x, y, rx0 + crit_w - pad_x, y + h),
                    text=TextSpec(crit, "semibold", size - 2, align="left"),
                )
            )
        for i, lines in enumerate(cells):
            x = rx0 + crit_w + i * cell_w
            layout.items.append(
                Item(
                    element_id=row.id, kind="text", style="cell",
                    rect=(x + pad_x, y, x + cell_w - pad_x, y + h),
                    text=TextSpec(lines, "regular", size),
                )
            )
        y += h

    return layout


def layout_comparison(plan: VisualPlan, region: Rect) -> Layout:
    if len(plan.columns) < 2 or not plan.rows:
        raise LayoutError("comparison needs at least 2 columns and 1 row")
    for size in range(26, 13, -2):
        result = _comparison_at(plan, region, size, force=False)
        if result is not None:
            return result
    return _comparison_at(plan, region, 14, force=True)


# ---------------------------------------------------------------------
# Key points
# ---------------------------------------------------------------------

def _key_points_at(plan: VisualPlan, region: Rect, size: int) -> Layout | None:
    rx0, ry0, rx1, ry1 = region
    W, H = rx1 - rx0, ry1 - ry0
    marker_w = size * (2.0 if any(p.icon for p in plan.points) else 1.4)
    gap = size * 0.9
    blocks = []
    for point in plan.points:
        if point.kind == "text":
            lines = wrap_text(point.text, "regular", size, W - marker_w - 20)
            if len(lines) > 3:
                return None
            blocks.append((point, lines, "regular", _text_height(lines, "regular", size)))
        elif point.kind == "formula" and _formula_fits(point.text, size, W - marker_w - 60):
            px, (fw, fh) = _formula_fits(point.text, size, W - marker_w - 60)
            blocks.append((point, [point.text], "formula", fh + 24))
        else:
            mono_size = max(size - 4, 14)
            raw_lines = point.text.splitlines() or [point.text]
            lines = []
            for raw in raw_lines:
                lines.extend(wrap_text(raw, "mono", mono_size, W - marker_w - 60) if raw.strip() else [""])
            if len(lines) > 8:
                return None
            blocks.append((point, lines, "mono", _text_height(lines, "mono", mono_size) + 28))

    total_h = sum(b[3] for b in blocks) + gap * (len(blocks) - 1)
    if total_h > H:
        return None

    layout = Layout(items=[], order=[p.id for p in plan.points], font_size=size)
    y = ry0 + (H - total_h) / 2
    for point, lines, role, h in blocks:
        layout.items.append(
            Item(
                element_id=point.id, kind="band", style="point_marker",
                icon=point.icon,
                rect=(rx0, y, rx0 + marker_w, y + min(h, size * 1.3)),
            )
        )
        if role == "formula":
            layout.items.append(
                Item(
                    element_id=point.id, kind="box", style="formula",
                    rect=(rx0 + marker_w, y, rx1 - 20, y + h),
                    latex=point.text,
                    text=TextSpec([], "regular", _formula_fits(point.text, size, W - marker_w - 60)[0]),
                )
            )
        elif role == "mono":
            layout.items.append(
                Item(
                    element_id=point.id, kind="box", style="code",
                    rect=(rx0 + marker_w, y, rx1 - 20, y + h),
                    text=TextSpec(lines, "mono", max(size - 4, 14), align="left"),
                )
            )
        else:
            layout.items.append(
                Item(
                    element_id=point.id, kind="text", style="point",
                    rect=(rx0 + marker_w, y, rx1 - 20, y + h),
                    text=TextSpec(lines, "regular", size, align="left"),
                )
            )
        y += h + gap
    return layout


def _formula_fits(latex: str, size: int, max_width: float):
    """(pixel size, (width, height)) of the largest rendering that fits, or None."""
    from app.video.math_render import render_formula

    for px in range(int(size * 1.3), 13, -2):
        image = render_formula(latex, px)
        if image is None:
            return None
        if image.width <= max_width:
            return px, image.size
    return None


def layout_key_points(plan: VisualPlan, region: Rect) -> Layout:
    if not plan.points:
        raise LayoutError("key_points has no points")
    for size in range(min(32, int((region[2] - region[0]) / 16)), 15, -2):
        result = _key_points_at(plan, region, size)
        if result is not None:
            return result
    raise LayoutError(f"{len(plan.points)} key points do not fit")


def layout_illustration(plan: VisualPlan, region: Rect) -> Layout:
    """Picture (aspect-preserving) with credit on the left, points on the right."""
    from PIL import Image

    if not plan.image_path:
        raise LayoutError("illustration has no image")
    rx0, ry0, rx1, ry1 = region
    W, H = rx1 - rx0, ry1 - ry0
    has_points = bool(plan.points)
    image_w = W * (0.56 if has_points else 1.0)
    credit_size = 13
    credit_lines = _truncate(wrap_text(plan.image_credit, "regular", credit_size, image_w), 2)
    credit_h = _text_height(credit_lines, "regular", credit_size) + 8 if plan.image_credit else 0

    with Image.open(plan.image_path) as img:
        iw, ih = img.size
    scale = min(image_w / iw, (H - credit_h) / ih)
    w, h = iw * scale, ih * scale
    x0 = rx0 + (image_w - w) / 2
    y0 = ry0 + (H - credit_h - h) / 2

    items = [Item(element_id="image", kind="box", style="image",
                  rect=(x0, y0, x0 + w, y0 + h), image_path=plan.image_path)]
    if credit_lines and plan.image_credit:
        items.append(Item(element_id="image", kind="text", style="credit",
                          rect=(rx0, y0 + h + 6, rx0 + image_w, y0 + h + credit_h),
                          text=TextSpec(credit_lines, "regular", credit_size)))
    layout = Layout(items=items, order=["image"], font_size=0)
    if has_points:
        side = (rx0 + image_w + 40, ry0, rx1, ry1)
        points = layout_key_points(plan, side)
        layout.items.extend(points.items)
        layout.order.extend(points.order)
        layout.font_size = points.font_size
    return layout


def layout_visual(plan: VisualPlan, region: Rect) -> Layout:
    builder = {
        "illustration": layout_illustration,
        "concept_map": layout_concept_map,
        "process_flow": layout_process_flow,
        "comparison": layout_comparison,
        "key_points": layout_key_points,
    }[plan.visual_type]
    return builder(plan, region)
